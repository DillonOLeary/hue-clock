"""The whole stack against real SQLite: a scripted bridge produces lamp
events, the system records them, the flusher publishes them, and a process
restart finds everything still there."""

import contextlib
import datetime as dt
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hue_clock.application.time_tracking import ClockedOut
from hue_clock.projections.capacities_note.flusher import NoteFlusher
from hue_clock.runtime.daemon import Daemon
from hue_clock.runtime.hue_listener import HueListener
from hue_clock.runtime.tracker_runtime import TrackerRuntime

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

    def test_shutdown_clocks_out_publishes_and_is_idempotent(self):
        runtime = TrackerRuntime.start(env=self.env)
        self.addCleanup(runtime.stop)
        bridge = ScriptedBridge(initially_on=True, batches=[])
        publisher = RecordingPublisher()
        flusher = NoteFlusher(runtime.notes, publisher, lock=runtime.commands)
        listener = HueListener(runtime, bridge, "Focus Lamp")
        daemon = Daemon(lock=io.StringIO(), runtime=runtime, listener=listener, flusher=flusher)

        with contextlib.redirect_stdout(io.StringIO()):
            listener.run()  # reconciles against the lit lamp: clocked in
            daemon.shutdown()
            daemon.shutdown()  # second quit path firing must record nothing new

        clock_in, clock_out = publisher.published
        self.assertTrue(clock_in.startswith("🟢 "))
        self.assertTrue(clock_out.startswith("🔴 "))
        self.assertNotIn("approx", clock_out)  # quit time is exact
        now = dt.datetime.now()
        self.assertEqual(runtime.clock_status(now), ClockedOut())
        self.assertEqual(runtime.queue_status(now).pending, 0)

    def test_observe_publish_and_survive_restart(self):
        runtime = TrackerRuntime.start(env=self.env)
        bridge = ScriptedBridge(initially_on=True, batches=[lamp_event(False)])
        publisher = RecordingPublisher()
        flusher = NoteFlusher(runtime.notes, publisher, lock=runtime.commands)

        with contextlib.redirect_stdout(io.StringIO()):
            HueListener(runtime, bridge, "Focus Lamp").run()
            flusher.flush()

        clock_in, clock_out = publisher.published
        self.assertTrue(clock_in.startswith("🟢 "))
        self.assertTrue(clock_in.endswith(" *(approx)*"))
        self.assertTrue(clock_out.startswith("🔴 "))

        now = dt.datetime.now()
        self.assertEqual(runtime.clock_status(now), ClockedOut())
        self.assertEqual(runtime.queue_status(now).pending, 0)
        runtime.stop()

        reborn = TrackerRuntime.start(env=self.env)
        self.addCleanup(reborn.stop)
        self.assertEqual(reborn.clock_status(now), ClockedOut())
        self.assertEqual(reborn.day_summary(now).session_count, 1)
        self.assertEqual(reborn.queue_status(now).pending, 0)


if __name__ == "__main__":
    unittest.main()
