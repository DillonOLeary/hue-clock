import datetime as dt
import time

from hue_clock.adapters.hue_bridge import HueBridge
from hue_clock.domain.work_day import Provenance
from hue_clock.runtime.composition import TrackerRuntime

RECONNECT_DELAY_S = 5


class HueListener:
    def __init__(self, runtime: TrackerRuntime, bridge: HueBridge, light_name: str) -> None:
        self.runtime = runtime
        self.bridge = bridge
        self.light_id = bridge.find_light(light_name)["id"]
        print(f"watching light {light_name!r} ({self.light_id}) on bridge {bridge.ip}", flush=True)

    def run(self) -> None:
        while True:
            try:
                stream = self.bridge.open_event_stream()
                self._reconcile()
                self.runtime.flusher_wake.set()
                for events in self.bridge.iter_events(stream):
                    self._dispatch(events)
            except KeyboardInterrupt:
                return
            except Exception as error:
                if "timed out" in str(error).lower():
                    continue
                print(f"stream error, reconnecting in {RECONNECT_DELAY_S}s: {error}", flush=True)
                time.sleep(RECONNECT_DELAY_S)

    def _dispatch(self, events) -> None:
        for event in events:
            for item in event.get("data", []):
                if item.get("id") != self.light_id or "on" not in item:
                    continue
                self.runtime.record_lamp_state(item["on"]["on"], dt.datetime.now())

    def _reconcile(self) -> None:
        """The lamp may have changed while we weren't listening; the true
        transition time is unrecoverable, so the record is marked approximate.
        """
        live_on = self.bridge.light_is_on(self.light_id)
        now = dt.datetime.now()
        self.runtime.advance_to(now)
        status = self.runtime.clock_status(now)
        known_on = status.is_clocked_in if status else False
        if known_on != live_on:
            self.runtime.record_lamp_state(live_on, now, Provenance.RECONCILED)
