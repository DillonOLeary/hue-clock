import fcntl
import os
from typing import TextIO

from hue_clock.runtime.config import LOCK_FILE


def acquire_single_instance_lock() -> TextIO:
    """Prevent two listeners from double-logging. Returns the held lock file."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")  # noqa: SIM115 — held for the process lifetime
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another hue_clock listener is already running — exiting") from None
    lock.write(str(os.getpid()))
    lock.flush()
    return lock
