# -*- coding: utf-8 -*-
"""Cross-process API concurrency control for D2C_13_2.

The experiment launcher starts one Python process per corpus run.  A process-local
semaphore is therefore insufficient for a global API budget.  This module uses a
small fixed set of file locks (plus a thread semaphore) so all participating
processes share the same maximum concurrency.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None
try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None

_CONFIG_LOCK = threading.RLock()
_LIMIT = 0
_THREAD_SEM: threading.BoundedSemaphore | None = None
_SLOT_DIR: Path | None = None


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return max(minimum, default)


def configure(limit: int | None = None) -> int:
    """Configure the shared API budget; 0 disables the global gate."""
    global _LIMIT, _THREAD_SEM, _SLOT_DIR
    raw = limit if limit is not None else _env_int("D2C11_API_MAX_CONCURRENCY", 0, minimum=0)
    value = max(0, int(raw))
    with _CONFIG_LOCK:
        if value == _LIMIT and (value == 0 or (_THREAD_SEM is not None and _SLOT_DIR is not None)):
            return value
        _LIMIT = value
        if value == 0:
            _THREAD_SEM = None
            _SLOT_DIR = None
            return value
        _THREAD_SEM = threading.BoundedSemaphore(value)
        _SLOT_DIR = Path(tempfile.gettempdir()) / "d2c11_api_slots" / str(value)
        _SLOT_DIR.mkdir(parents=True, exist_ok=True)
        for index in range(value):
            slot = _SLOT_DIR / f"slot_{index:02d}.lock"
            slot.touch(exist_ok=True)
            if slot.stat().st_size == 0:
                slot.write_bytes(b"0")
    return value


def _try_lock(handle) -> bool:
    if msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    if fcntl is not None:  # pragma: no cover - POSIX
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    return True


def _unlock(handle) -> None:
    if msvcrt is not None:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    elif fcntl is not None:  # pragma: no cover - POSIX
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def api_slot(timeout: float | None = None):
    """Acquire one global API slot across threads and D2C_13_2 processes."""
    if _THREAD_SEM is None or _SLOT_DIR is None:
        if configure() == 0:
            yield
            return
    assert _THREAD_SEM is not None and _SLOT_DIR is not None
    timeout = float(timeout if timeout is not None else _env_int("D2C11_API_SLOT_TIMEOUT", 1800))
    if not _THREAD_SEM.acquire(timeout=timeout):
        raise TimeoutError("D2C_13_2 API thread semaphore timeout")
    handle = None
    try:
        deadline = time.monotonic() + timeout
        while handle is None:
            for slot in sorted(_SLOT_DIR.glob("slot_*.lock")):
                candidate = slot.open("a+b")
                if _try_lock(candidate):
                    handle = candidate
                    break
                candidate.close()
            if handle is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("D2C_13_2 global API semaphore timeout")
                time.sleep(0.05)
        yield
    finally:
        if handle is not None:
            _unlock(handle)
            handle.close()
        _THREAD_SEM.release()


def relation_workers(default: int = 4) -> int:
    return _env_int("D2C11_RELATION_WORKERS", default)


def verifier_workers(default: int = 4) -> int:
    return _env_int("D2C11_VERIFIER_WORKERS", default)
