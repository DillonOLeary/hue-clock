import datetime as dt
import threading
from dataclasses import dataclass
from typing import TextIO

from hue_clock.adapters.capacities_api import CapacitiesClient
from hue_clock.adapters.hue_bridge import HueBridge
from hue_clock.adapters.note_publisher import CapacitiesNotePublisher
from hue_clock.domain.work_day import Provenance
from hue_clock.projections.capacities_note.flusher import NoteFlusher
from hue_clock.runtime.config import load_config, require
from hue_clock.runtime.hue_listener import HueListener
from hue_clock.runtime.instance_lock import acquire_single_instance_lock
from hue_clock.runtime.note_flusher_loop import FlusherLoop
from hue_clock.runtime.tracker_runtime import TrackerRuntime

# shutdown() blocks on one flush attempt at most this long; anything unsent
# stays queued in SQLite and drains on next launch.
SHUTDOWN_FLUSH_TIMEOUT_S = 10


@dataclass
class Daemon:
    """The assembled stack: collaborators by name, plus lifecycle.

    UIs hold this for shutdown() and reach commands/queries through
    `runtime`; they never touch the flusher or provenance directly.
    """

    lock: TextIO
    runtime: TrackerRuntime
    listener: HueListener
    flusher: NoteFlusher

    def shutdown(self) -> None:
        """Clock out, then give queued lines one bounded shot at Capacities.

        The lamp is left as-is; if it's still on at next launch, reconcile
        clocks back in. A failed or slow flush is fine — lines stay queued
        in SQLite and drain on next launch.
        """
        now = dt.datetime.now()
        try:
            if self.runtime.record_clock_state(False, now, Provenance.QUIT):
                print(f"shutdown: clocked out at {now.isoformat(timespec='seconds')}", flush=True)
        except Exception as e:
            print(f"shutdown: clock-out failed: {e}", flush=True)
            return
        # flush() swallows publish errors itself; the thread + join bounds how
        # long a hung network call can hold up process exit.
        flush = threading.Thread(target=self.flusher.flush, daemon=True, name="shutdown-flush")
        flush.start()
        flush.join(SHUTDOWN_FLUSH_TIMEOUT_S)
        if flush.is_alive():
            print("shutdown: flush still in flight at exit; lines stay queued", flush=True)


def start_daemon() -> Daemon:
    """Assemble the full tracking stack.

    Wires the instance lock, event-sourced runtime, flusher thread, and a
    ready-to-run bridge listener.
    """
    lock = acquire_single_instance_lock()
    config = load_config()
    light_name = require(config.light_name, "FOCUS_LIGHT_NAME")
    token = require(config.capacities_token, "CAPACITIES_API_TOKEN")
    bridge = HueBridge(
        require(config.bridge_ip, "HUE_BRIDGE_IP"),
        require(config.bridge_key, "HUE_APP_KEY"),
    )
    runtime = TrackerRuntime.start()
    publisher = CapacitiesNotePublisher(CapacitiesClient(token))
    flusher = NoteFlusher(runtime.notes, publisher, lock=runtime.commands)
    loop = FlusherLoop(flusher)
    runtime.on_recorded(loop.wake)
    loop.start_thread()
    listener = HueListener(runtime, bridge, light_name, on_reconnect=loop.wake)
    return Daemon(lock, runtime, listener, flusher)
