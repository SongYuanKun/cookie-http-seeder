"""An OS-held, nonblocking lock for one receiver per local data directory.

Keep the lock file after close: unlinking it would let contenders lock different
inodes. The OS releases the lock if the owner exits or crashes. This is not a
network-filesystem/distributed lock and does not serialize arbitrary file editors.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class DataDirectoryInUse(RuntimeError):
    """Another receiver owns this directory; do not start a second writer."""


class DataDirectoryLock:
    def __init__(self, directory: Path):
        self.path = Path(directory) / ".receiver.lock"
        self._fd: int | None = None

    def __enter__(self) -> DataDirectoryLock:
        if self._fd is not None:
            raise RuntimeError("data directory lock is not reentrant")
        # The receiver creates the private directory before acquiring the lock.
        if self.path.is_symlink():
            raise ValueError("receiver lock must not be a symbolic link")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("receiver lock must be a regular unshared file")
            os.set_inheritable(fd, False)
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise RuntimeError("data directory locking is unsupported on this OS")
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise DataDirectoryInUse(
                        "data directory already in use by a receiver"
                    ) from None
                raise
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
