"""Bounded subprocesses; repository instructions are never run on the host."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


class RepositoryError(Exception):
    def __init__(self, code, log=""):
        self.code, self.log = code, log
        super().__init__(code)


def run_process(argv, *, cwd, env=None, timeout=120, cancelled=lambda: False, monitor=None):
    """Drain output without unbounded memory/disk use and kill the process group on interruption."""
    if cancelled():
        raise RepositoryError("cancel_requested")
    try:
        # A separate watchdog owns the command's process group. Pipe EOF means the
        # assessment process died, even if its finally blocks never ran.
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--supervise", *argv],
                                cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        raise RepositoryError("command_unavailable") from None
    output = bytearray()
    overflow = threading.Event()

    def read():
        while chunk := proc.stdout.read(8192):
            available = 1024 * 1024 - len(output)
            output.extend(chunk[:max(0, available)])
            if len(chunk) > available:
                overflow.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline, next_monitor = time.monotonic() + timeout, 0
    reason = None
    try:
        while proc.poll() is None:
            if cancelled():
                reason = "cancel_requested"
            elif time.monotonic() >= deadline:
                reason = "command_timeout"
            elif overflow.is_set():
                reason = "command_output_limit"
            if reason:
                break
            if monitor and time.monotonic() >= next_monitor:
                monitor()
                next_monitor = time.monotonic() + 0.5
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()
        proc.stdin.close()
        reader.join(timeout=2)
        proc.stdout.close()
    log = output.decode(errors="replace")
    if reason or overflow.is_set() or proc.returncode:
        raise RepositoryError(reason or ("command_output_limit" if overflow.is_set() else
                                         "command_unavailable" if proc.returncode == 127 else "command_failed"), log)
    return log


def supervise(argv):
    def parent_watchdog():
        # A daemon thread must not hold Python's buffered stdin lock at shutdown.
        while os.read(0, 1):
            pass
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=parent_watchdog, daemon=True).start()
    try:
        # Inherit the supervisor group so cancellation also stops Git helper processes.
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        return child.wait()
    except OSError:
        return 127


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--supervise":
        raise SystemExit(2)
    raise SystemExit(supervise(sys.argv[2:]))
