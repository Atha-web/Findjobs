"""
A small cross-process lock, so the Telegram poller and scheduled agent runs (separate
Python processes) can't overwrite each other's read-modify-write of the JSON data files.
threading.Lock alone only protects threads inside one process.

Usage:
    with locked("tracker"):
        data = load(); ...; save(data)

Re-entrant within a thread, so a function holding the lock can call another that takes it.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCK_DIR = os.path.join(_DIR, "data")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCK = threading.Lock()
_HELD = threading.local()  # per-thread {name: depth}, for re-entrancy

TIMEOUT_SECONDS = 30


def _thread_lock(name: str) -> threading.RLock:
    with _REGISTRY_LOCK:
        return _THREAD_LOCKS.setdefault(name, threading.RLock())


def _acquire_file(fh) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Could not acquire the data file lock (another process is holding it).")
            time.sleep(0.05)


def _release_file(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


@contextmanager
def locked(name: str):
    depths = getattr(_HELD, "depths", None)
    if depths is None:
        depths = _HELD.depths = {}

    with _thread_lock(name):
        if depths.get(name, 0) > 0:  # already held by this thread: just nest
            depths[name] += 1
            try:
                yield
            finally:
                depths[name] -= 1
            return

        os.makedirs(_LOCK_DIR, exist_ok=True)
        fh = open(os.path.join(_LOCK_DIR, f"{name}.lock"), "a+b")
        try:
            _acquire_file(fh)
        except BaseException:
            fh.close()
            raise
        depths[name] = 1
        try:
            yield
        finally:
            depths[name] = 0
            _release_file(fh)
