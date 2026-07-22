"""The whole stack against real SQLite: a scripted bridge produces lamp
events, the system records them, the flusher publishes them, and a process
restart finds everything still there."""

import contextlib
import datetime as dt
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hue_clock.projections.capacities_note.flusher import NoteFlusher
from hue_clock.runtime.composition import TrackerRuntime
from hue_clock.runtime.hue_listener import HueListener

LIGHT_ID = "light-1"


class ScriptedBridge:
    ip = "scripted"

    def __init__(self, initially_on, batches):
        self.initially_on = initially_on
        self.batches = batches

    def find_light(self, name):
        return {"id": LIGHT_ID}

    def light_is_on(self, light_id):
        return self.initially_on

    def open_event_stream(self):
        return None

    def iter_events(self, stream):
        yield from self.batches
        raise KeyboardInterrupt


class RecordingPublisher:
    def __init__(self):
        self.published = []

    def append(self, line, day):
        self.published.append(line)

    def line_present(self, line, day):
        return line in self.published

    def scrub_adjacent_duplicates(self, day):
        return 0


def lamp_event(on):
    return [{"data": [{"id": LIGHT_ID, "on": {"on": on}}]}]


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "TIMETRACKING_SQLITE_DBNAME": f"file:{Path(tmp.name) / 'tracking.db'}",
            "CAPACITIESNOTEPROJECTION_SQLITE_DBNAME": f"file:{Path(tmp.name) / 'notes.db'}",
        }

    def test_observe_publish_and_survive_restart(self):
        runtime = TrackerRuntime.start(env=self.env)
        bridge = ScriptedBridge(initially_on=True, batches=[lamp_event(False)])
        publisher = RecordingPublisher()
        flusher = NoteFlusher(
            runtime.notes, publisher, settle=lambda seconds: None, lock=runtime.commands
        )

        with contextlib.redirect_stdout(io.StringIO()):
            HueListener(runtime, bridge, "Focus Lamp").run()
            flusher.flush()

        clock_in, clock_out = publisher.published
        self.assertTrue(clock_in.startswith("🟢 "))
        self.assertTrue(clock_in.endswith(" *(approx)*"))
        self.assertTrue(clock_out.startswith("🔴 "))

        now = dt.datetime.now()
        self.assertFalse(runtime.clock_status(now).is_clocked_in)
        self.assertEqual(runtime.queue_status(now).pending, 0)
        runtime.stop()

        reborn = TrackerRuntime.start(env=self.env)
        self.addCleanup(reborn.stop)
        status = reborn.clock_status(now)
        self.assertIsNotNone(status)
        self.assertFalse(status.is_clocked_in)
        self.assertEqual(reborn.day_summary(now).session_count, 1)
        self.assertEqual(reborn.queue_status(now).pending, 0)


if __name__ == "__main__":
    unittest.main()
