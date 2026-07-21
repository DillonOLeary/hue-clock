import fcntl
import os

from hue_clock.runtime.config import LOCK_FILE


def acquire_single_instance_lock():
    """Prevent two listeners from double-logging. Returns the held lock file."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another hue_clock listener is already running — exiting")
    lock.write(str(os.getpid()))
    lock.flush()
    return lock
