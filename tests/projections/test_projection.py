import contextlib
import datetime as dt
import io
import unittest

from eventsourcing.system import SingleThreadedRunner, System

from hue_clock.application.time_tracking import TimeTracking
from hue_clock.domain.work_day import Provenance
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0, day=DAY):
    return dt.datetime.combine(day, dt.time(hour, minute))


class ProjectionTest(unittest.TestCase):
    def setUp(self):
        system = System(pipes=[[TimeTracking, CapacitiesNoteProjection]])
        self.runner = SingleThreadedRunner(system)
        self.runner.start()
        self.addCleanup(self.runner.stop)
        self.tracking = self.runner.get(TimeTracking)
        self.notes = self.runner.get(CapacitiesNoteProjection)

    def record(self, *transitions):
        with contextlib.redirect_stdout(io.StringIO()):
            for on, when in transitions:
                self.tracking.record_lamp_state(on, when)

    def test_transitions_become_queued_note_lines(self):
        self.record((True, at(9, 12)), (False, at(12, 40)))
        note = self.notes.note(DAY)
        self.assertEqual(
            [line.text for line in note.queue],
            ["🟢 9:12a", "🔴 12:40p · 3h 28m"],
        )

    def test_break_math_spans_the_whole_day_fold(self):
        self.record(
            (True, at(9, 12)), (False, at(12, 40)),
            (True, at(13, 32)), (False, at(17, 40)),
        )
        note = self.notes.note(DAY)
        self.assertEqual(note.queue[2].text, "🟢 1:32p · 52m break")
        self.assertEqual(note.queue[3].text, "🔴 5:40p · 4h 08m · Σ 7h 36m")

    def test_rollover_produces_no_lines_but_correct_math(self):
        day2 = DAY + dt.timedelta(days=1)
        self.record((True, at(21)), (False, at(1, day=day2)))

        first_note = self.notes.note(DAY)
        self.assertEqual([line.text for line in first_note.queue], ["🟢 9:00p"])
        self.assertFalse(first_note.ledger.is_clocked_in)

        second_note = self.notes.note(day2)
        self.assertEqual([line.text for line in second_note.queue], ["🔴 1:00a · 1h 00m"])

    def test_imported_history_is_archived_not_queued(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.tracking.record_lamp_state(True, at(9), Provenance.IMPORTED)
            self.tracking.record_lamp_state(False, at(10), Provenance.IMPORTED)
        note = self.notes.note(DAY)
        self.assertEqual(note.queue, [])
        self.assertEqual(len(note.ledger.closed_sessions), 1)

    def test_strikes_flow_through(self):
        self.record((True, at(9)), (False, at(12)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.tracking.strike_span(at(10), at(11), at(12, 30))
        note = self.notes.note(DAY)
        self.assertEqual(note.queue[-1].text, "⚫ 10:00a–11:00a · −1h 00m")

    def test_queue_status_aggregates_recent_days(self):
        self.record((True, at(9, 12)), (False, at(12, 40)))
        status = self.notes.queue_status(DAY)
        self.assertEqual(status.pending, 2)
        self.assertFalse(status.head_sent)
        self.assertIsNone(status.last_confirmed_at)
        self.assertEqual(self.notes.days_with_pending(DAY), [DAY])

    def test_queued_lines_are_printed_for_the_log(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.tracking.record_lamp_state(True, at(9, 12))
        self.assertEqual(buffer.getvalue(), "[2026-07-21T09:12:00] 🟢 9:12a\n")


if __name__ == "__main__":
    unittest.main()
