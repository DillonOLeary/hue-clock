import datetime as dt
import unittest

from hue_clock.domain.work_day import Provenance
from hue_clock.projections.capacities_note.daily_note import DailyNote

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0):
    return dt.datetime(2026, 7, 21, hour, minute)


QUEUED_AT = at(20)  # wall-clock queue time; distinct from the transition time


class DailyNoteTest(unittest.TestCase):
    def test_observed_transition_queues_a_line(self):
        note = DailyNote(DAY)
        queued = note.record_transition("in", at(9, 12), Provenance.OBSERVED, QUEUED_AT)
        self.assertEqual(queued, "🟢 9:12a")
        self.assertEqual(note.head.text, "🟢 9:12a")
        self.assertEqual(note.head.first_queued_at, QUEUED_AT)
        self.assertTrue(note.ledger.is_clocked_in)

    def test_reconciled_transition_queues_an_approx_line(self):
        note = DailyNote(DAY)
        queued = note.record_transition("in", at(9, 12), Provenance.RECONCILED, QUEUED_AT)
        self.assertEqual(queued, "🟢 9:12a *(approx)*")

    def test_rollover_extends_ledger_without_queueing(self):
        note = DailyNote(DAY)
        self.assertIsNone(note.record_transition("in", at(0), Provenance.ROLLOVER, QUEUED_AT))
        self.assertEqual(note.queue, [])
        self.assertTrue(note.ledger.is_clocked_in)

    def test_imported_transition_extends_ledger_without_queueing(self):
        note = DailyNote(DAY)
        self.assertIsNone(note.record_transition("in", at(9), Provenance.IMPORTED, QUEUED_AT))
        self.assertEqual(note.queue, [])
        self.assertTrue(note.ledger.is_clocked_in)

    def test_line_math_rides_on_the_fold_even_for_unpublished_transitions(self):
        note = DailyNote(DAY)
        note.record_transition("in", at(9), Provenance.IMPORTED, QUEUED_AT)
        note.record_transition("out", at(10), Provenance.IMPORTED, QUEUED_AT)
        queued = note.record_transition("in", at(10, 30), Provenance.OBSERVED, QUEUED_AT)
        self.assertEqual(queued, "🟢 10:30a · 30m break")

    def test_observed_strike_queues_a_line(self):
        note = DailyNote(DAY)
        queued = note.record_strike(at(13, 32), at(14, 2), Provenance.OBSERVED, QUEUED_AT)
        self.assertEqual(queued, "⚫ 1:32p–2:02p · −30m")

    def test_imported_strike_is_archived_silently(self):
        note = DailyNote(DAY)
        self.assertIsNone(note.record_strike(at(13), at(14), Provenance.IMPORTED, QUEUED_AT))
        self.assertEqual(note.queue, [])
        self.assertEqual(len(note.ledger.strikes), 1)

    def test_head_lifecycle(self):
        # The retired read-back events (head_sent/head_resent) still replay onto
        # the queued line; head_confirmed pops it. Kept as replay coverage.
        note = DailyNote(DAY)
        note.record_transition("in", at(9), Provenance.OBSERVED, QUEUED_AT)
        note.record_transition("out", at(10), Provenance.OBSERVED, QUEUED_AT)

        self.assertEqual(note.head.first_queued_at, QUEUED_AT)
        self.assertFalse(note.head.is_sent)
        note.head_sent(at(10, 0))
        self.assertTrue(note.head.is_sent)
        self.assertEqual(note.head.resends, 0)

        note.head_resent(at(10, 15))
        self.assertEqual(note.head.resends, 1)

        note.head_confirmed(at(10, 16))
        self.assertEqual(note.last_confirmed_at, at(10, 16))
        self.assertEqual(note.head.text, "🔴 10:00a · 1h 00m")
        self.assertFalse(note.head.is_sent)


if __name__ == "__main__":
    unittest.main()
