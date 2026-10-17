"""Reentrant thread + process lock for revision checks on the Linux Edge server.

The lock file is permanent; unlinking an active flock file would break exclusion.
Each Storage instance opens its own descriptor, including within one process.
"""
import fcntl
import threading
from pathlib import Path


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.mutex = threading.RLock()
        self.depth = 0
        self.stream = None

    def __enter__(self):
        self.mutex.acquire()
        try:
            if not self.depth:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.stream = self.path.open('a+b')
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
            self.depth += 1
            return self
        except BaseException:
            if self.stream:
                self.stream.close()
            self.mutex.release()
            raise

    def __exit__(self, *args):
        try:
            self.depth -= 1
            if not self.depth:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
                self.stream.close()
                self.stream = None
        finally:
            self.mutex.release()
