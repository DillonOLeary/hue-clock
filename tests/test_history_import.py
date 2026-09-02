import datetime as dt
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from hue_clock.history_import import import_history, parse_log_lines
from hue_clock.runtime.tracker_runtime import TrackerRuntime

DAY = dt.date(2026, 7, 21)

SAMPLE_LOG = """\
--- Hue Clock menu bar app started 2026-07-21T10:59:51.008816
listener crashed: <urlopen error [Errno 65] No route to host>
watching light 'Junior Lamp' (2be4df58) on bridge 10.11.83.17
[2026-07-21T11:05:07] 🟢 11:05a
[2026-07-21T11:34:51] 🔴 11:34a · 29m
append vanished (attempt 1/2): 🔴 11:34a · 29m
capacities write failed — 1 line(s) queued: appends keep vanishing
[2026-07-21T13:32:00] 🟢 1:32p · 1h 57m break
[2026-07-21T14:02:10] ⚫ 1:32p–2:02p · −30m
[2026-07-21T14:45:08] 🔴 2:45p · 1h 13m · Σ 1h 42m
"""


class ParseLogLinesTest(unittest.TestCase):
    def test_transitions_parsed_and_diagnostics_ignored(self):
        transitions = parse_log_lines(SAMPLE_LOG)
        self.assertEqual([t.mark for t in transitions], ["🟢", "🔴", "🟢", "⚫", "🔴"])
        self.assertEqual(transitions[0].at, dt.datetime(2026, 7, 21, 11, 5, 7))

    def test_strike_window_parsed_as_datetimes(self):
        strike = parse_log_lines(SAMPLE_LOG)[3]
        self.assertEqual(
            strike.strike_span,
            (dt.datetime(2026, 7, 21, 13, 32), dt.datetime(2026, 7, 21, 14, 2)),
        )

    def test_midnight_crossing_strike_starts_the_day_before(self):
        line = "[2026-07-22T00:10:00] ⚫ 11:50p–12:10a · −20m"
        strike = parse_log_lines(line)[0]
        self.assertEqual(
            strike.strike_span,
            (dt.datetime(2026, 7, 21, 23, 50), dt.datetime(2026, 7, 22, 0, 10)),
        )


class ImportHistoryTest(unittest.TestCase):
    def setUp(self):
        self.runtime = TrackerRuntime.start(env={})
        self.addCleanup(self.runtime.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "hue-clock.log"
        self.log_path.write_text(SAMPLE_LOG)

    def import_log(self):
        with redirect_stdout(io.StringIO()) as out:
            import_history(str(self.log_path), runtime=self.runtime)
        return out.getvalue()

    def test_import_rebuilds_the_ledger(self):
        self.import_log()
        summary = self.runtime.tracking.day_summary(DAY, dt.datetime(2026, 7, 21, 15))
        self.assertEqual(summary.session_count, 2)
        session_one = dt.datetime(2026, 7, 21, 11, 34, 51) - dt.datetime(2026, 7, 21, 11, 5, 7)
        session_two = dt.datetime(2026, 7, 21, 14, 45, 8) - dt.datetime(2026, 7, 21, 13, 32)
        worked = session_one.total_seconds() + session_two.total_seconds() - 30 * 60
        self.assertEqual(summary.worked_seconds, worked)
        self.assertEqual(summary.struck_seconds, 30 * 60)

    def test_imported_lines_are_archived_not_queued(self):
        self.import_log()
        note = self.runtime.notes.note(DAY)
        self.assertEqual(note.queue, [])
        self.assertEqual(len(note.ledger.closed_sessions), 2)
        self.assertEqual(len(note.ledger.strikes), 1)

    def test_second_import_skips_existing_days(self):
        self.import_log()
        output = self.import_log()
        self.assertIn("skipped 2026-07-21", output)
        summary = self.runtime.tracking.day_summary(DAY, dt.datetime(2026, 7, 21, 15))
        self.assertEqual(summary.session_count, 2)


if __name__ == "__main__":
    unittest.main()
