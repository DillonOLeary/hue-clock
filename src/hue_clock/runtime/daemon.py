from hue_clock.adapters.capacities_api import CapacitiesClient
from hue_clock.adapters.hue_bridge import HueBridge
from hue_clock.adapters.note_publisher import CapacitiesNotePublisher
from hue_clock.projections.capacities_note.flusher import NoteFlusher
from hue_clock.runtime.composition import TrackerRuntime
from hue_clock.runtime.config import load_config, require
from hue_clock.runtime.hue_listener import HueListener
from hue_clock.runtime.instance_lock import acquire_single_instance_lock
from hue_clock.runtime.note_flusher_loop import FlusherLoop


def start_daemon() -> tuple[object, TrackerRuntime, HueListener]:
    """The full tracking stack: instance lock, event-sourced runtime,
    flusher thread, and a ready-to-run bridge listener."""
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
    FlusherLoop(flusher, runtime.flusher_wake).start_thread()
    return lock, runtime, HueListener(runtime, bridge, light_name)
