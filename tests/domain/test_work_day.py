import datetime as dt
import unittest

from hue_clock.domain.work_day import (
    AlreadyClockedIn,
    NotClockedIn,
    Provenance,
    SpanOutsideDay,
    TransitionOutOfOrder,
    WorkDay,
)

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0):
    return dt.datetime(2026, 7, 21, hour, minute)


class WorkDayTest(unittest.TestCase):
    def test_id_is_deterministic_per_day(self):
        self.assertEqual(WorkDay.create_id(DAY), WorkDay.create_id(DAY))
        self.assertNotEqual(WorkDay.create_id(DAY), WorkDay.create_id(DAY + dt.timedelta(days=1)))

    def test_clocking_in_and_out_builds_the_ledger(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.OBSERVED)
        day.clock_out(at(12), Provenance.OBSERVED)
        self.assertFalse(day.is_clocked_in)
        self.assertEqual(day.ledger.worked_seconds(at(13)), 3 * 3600)

    def test_double_clock_in_is_rejected(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.OBSERVED)
        with self.assertRaises(AlreadyClockedIn):
            day.clock_in(at(10), Provenance.OBSERVED)

    def test_clock_out_without_open_session_is_rejected(self):
        day = WorkDay(DAY)
        with self.assertRaises(NotClockedIn):
            day.clock_out(at(10), Provenance.OBSERVED)

    def test_clock_out_before_session_start_is_rejected(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.OBSERVED)
        with self.assertRaises(TransitionOutOfOrder):
            day.clock_out(at(8), Provenance.OBSERVED)

    def test_zero_length_session_is_allowed(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.OBSERVED)
        day.clock_out(at(9), Provenance.OBSERVED)
        self.assertEqual(len(day.ledger.closed_sessions), 1)

    def test_strike_outside_the_day_is_rejected(self):
        day = WorkDay(DAY)
        tomorrow = at(9) + dt.timedelta(days=1)
        with self.assertRaises(SpanOutsideDay):
            day.strike(tomorrow, tomorrow + dt.timedelta(hours=1), at(9), Provenance.OBSERVED)

    def test_backwards_strike_is_rejected(self):
        day = WorkDay(DAY)
        with self.assertRaises(TransitionOutOfOrder):
            day.strike(at(10), at(9), at(10), Provenance.OBSERVED)

    def test_strike_reduces_worked_time(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.OBSERVED)
        day.clock_out(at(12), Provenance.OBSERVED)
        day.strike(at(10), at(11), at(12), Provenance.OBSERVED)
        self.assertEqual(day.ledger.worked_seconds(at(13)), 2 * 3600)

    def test_rejected_commands_emit_no_events(self):
        day = WorkDay(DAY)
        day.collect_events()
        with self.assertRaises(NotClockedIn):
            day.clock_out(at(10), Provenance.OBSERVED)
        self.assertEqual(day.collect_events(), [])

    def test_events_carry_occurred_at_and_provenance(self):
        day = WorkDay(DAY)
        day.clock_in(at(9), Provenance.RECONCILED)
        _opened, clocked_in = day.collect_events()
        self.assertIsInstance(clocked_in, WorkDay.ClockedIn)
        self.assertEqual(clocked_in.day, DAY)
        self.assertEqual(clocked_in.at, at(9))
        self.assertEqual(clocked_in.provenance, Provenance.RECONCILED)


if __name__ == "__main__":
    unittest.main()
