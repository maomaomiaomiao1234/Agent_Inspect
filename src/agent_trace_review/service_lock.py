"""One assessment owner per data directory, held until workers finish cleanup."""

import os


class ServiceLock:
    def __init__(self, root):
        self.stream = (root / ".assessment-service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.stream.write(b"0")
                self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError("此 data-dir 已有评审进程运行；请选择独立目录或先停止该服务。") from None

    def close(self):
        self.stream.close()
