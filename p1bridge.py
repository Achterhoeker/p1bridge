#!/usr/bin/env python3

import errno
import fcntl
import os
import pty
import re
import select
import signal
import sys
import termios
import time
import tty
from collections import deque

import serial


SERIAL_DEVICE = (
    "/dev/serial/by-id/"
    "usb-FTDI_FT232R_USB_UART_ALRSYKI-if00-port0"
)

RUNTIME_DIR = "/run/p1-bridge"
VIRTUAL_SERIAL = RUNTIME_DIR + "/tty"
POWER_FILE = "/run/p1-power-watt"

# Nooit meer dan dit aantal volledige DSMR-telegrammen bufferen.
# Bij achterstand worden de oudste telegrammen weggegooid.
MAX_QUEUED_TELEGRAMS = 2

IMPORT_PATTERN = re.compile(
    rb"^1-0:1\.7\.0\(([0-9.]+)\*kW\)"
)

EXPORT_PATTERN = re.compile(
    rb"^1-0:2\.7\.0\(([0-9.]+)\*kW\)"
)

running = True


def stop_service(signum, frame):
    global running
    running = False


def set_nonblocking(file_descriptor):
    flags = fcntl.fcntl(
        file_descriptor,
        fcntl.F_GETFL
    )

    fcntl.fcntl(
        file_descriptor,
        fcntl.F_SETFL,
        flags | os.O_NONBLOCK
    )


def create_virtual_serial():
    os.makedirs(
        RUNTIME_DIR,
        mode=0o755,
        exist_ok=True
    )

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    tty.setraw(master_fd)
    tty.setraw(slave_fd)

    attributes = termios.tcgetattr(slave_fd)

    attributes[4] = termios.B115200
    attributes[5] = termios.B115200

    termios.tcsetattr(
        slave_fd,
        termios.TCSANOW,
        attributes
    )

    set_nonblocking(master_fd)

    try:
        os.unlink(VIRTUAL_SERIAL)
    except FileNotFoundError:
        pass

    os.symlink(
        slave_name,
        VIRTUAL_SERIAL
    )

    os.chmod(
        slave_name,
        0o666
    )

    return master_fd, slave_fd, slave_name


def write_power(power_watt):
    temporary_file = POWER_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="ascii"
    ) as output:
        output.write(f"{power_watt}\n")
        output.flush()
        os.fsync(output.fileno())

    os.replace(
        temporary_file,
        POWER_FILE
    )


def queue_telegram(queue, telegram):
    while len(queue) >= MAX_QUEUED_TELEGRAMS:
        queue.popleft()

    queue.append(
        bytearray(telegram)
    )


def flush_telegram_queue(master_fd, queue):
    while queue:
        telegram = queue[0]

        if not telegram:
            queue.popleft()
            continue

        try:
            written = os.write(
                master_fd,
                telegram
            )

            if written > 0:
                del telegram[:written]

            if not telegram:
                queue.popleft()

        except BlockingIOError:
            # PTY-buffer is vol. Niet wachten.
            return

        except OSError as error:
            if error.errno in (
                errno.EAGAIN,
                errno.EWOULDBLOCK,
                errno.EIO
            ):
                # EIO betekent meestal dat ser2net de slave-PTY
                # nog niet geopend heeft.
                return

            raise


def open_p1_serial():
    return serial.Serial(
        port=SERIAL_DEVICE,
        baudrate=115200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
        exclusive=True
    )


def main():
    signal.signal(
        signal.SIGTERM,
        stop_service
    )

    signal.signal(
        signal.SIGINT,
        stop_service
    )

    master_fd, slave_fd, slave_name = (
        create_virtual_serial()
    )

    print(
        f"Virtual P1 serial port: "
        f"{VIRTUAL_SERIAL} -> {slave_name}",
        flush=True
    )

    p1 = open_p1_serial()

    print(
        f"Reading P1 data from {SERIAL_DEVICE}",
        flush=True
    )

    receive_buffer = bytearray()
    current_telegram = bytearray()
    telegram_queue = deque()

    import_kw = None
    export_kw = None
    in_telegram = False

    try:
        while running:
            read_list = [p1.fileno()]
            write_list = []

            if telegram_queue:
                write_list.append(master_fd)

            readable, writable, _ = select.select(
                read_list,
                write_list,
                [],
                1.0
            )

            if master_fd in writable:
                flush_telegram_queue(
                    master_fd,
                    telegram_queue
                )

            if p1.fileno() not in readable:
                continue

            try:
                data = os.read(
                    p1.fileno(),
                    4096
                )

            except BlockingIOError:
                continue

            if not data:
                raise RuntimeError(
                    "P1 serial device returned no data"
                )

            receive_buffer.extend(data)

            while b"\n" in receive_buffer:
                raw_line, separator, remainder = (
                    receive_buffer.partition(b"\n")
                )

                receive_buffer = bytearray(remainder)

                complete_line = (
                    raw_line
                    + separator
                )

                line = raw_line.strip()

                if line.startswith(b"/"):
                    current_telegram = bytearray()
                    current_telegram.extend(
                        complete_line
                    )

                    import_kw = None
                    export_kw = None
                    in_telegram = True
                    continue

                if not in_telegram:
                    continue

                current_telegram.extend(
                    complete_line
                )

                match = IMPORT_PATTERN.match(line)

                if match:
                    import_kw = float(
                        match.group(1)
                    )

                match = EXPORT_PATTERN.match(line)

                if match:
                    export_kw = float(
                        match.group(1)
                    )

                if not line.startswith(b"!"):
                    continue

                if (
                    import_kw is not None
                    and export_kw is not None
                ):
                    power_watt = round(
                        (
                            import_kw
                            - export_kw
                        )
                        * 1000
                    )

                    write_power(power_watt)

                    #print( power_watt, flush=True )

                queue_telegram(
                    telegram_queue,
                    current_telegram
                )

                flush_telegram_queue(
                    master_fd,
                    telegram_queue
                )

                current_telegram = bytearray()
                import_kw = None
                export_kw = None
                in_telegram = False

    finally:
        p1.close()

        os.close(master_fd)
        os.close(slave_fd)

        try:
            os.unlink(VIRTUAL_SERIAL)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    while running:
        try:
            main()

        except KeyboardInterrupt:
            break

        except Exception as error:
            print(
                f"Fatal error: {error}",
                file=sys.stderr,
                flush=True
            )

            if running:
                time.sleep(2)
