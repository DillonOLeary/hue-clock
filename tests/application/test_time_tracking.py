import datetime as dt
import unittest

from hue_clock.application.time_tracking import NoSuchSession, TimeTracking
from hue_clock.domain.work_day import Provenance, WorkDay

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0, day=DAY):
    return dt.datetime.combine(day, dt.time(hour, minute))


class TimeTrackingTest(unittest.TestCase):
    def setUp(self):
        self.app = TimeTracking()

    def test_no_history_means_unknown_status(self):
        self.assertIsNone(self.app.clock_status(at(9)))

    def test_lamp_on_clocks_in(self):
        self.assertTrue(self.app.record_lamp_state(True, at(9)))
        status = self.app.clock_status(at(10))
        self.assertTrue(status.is_clocked_in)
        self.assertEqual(status.since, at(9))

    def test_unchanged_lamp_state_is_a_no_op(self):
        self.app.record_lamp_state(True, at(9))
        self.assertFalse(self.app.record_lamp_state(True, at(10)))
        self.assertEqual(self.app.day_summary(DAY, at(11)).session_count, 1)

    def test_lamp_off_without_history_records_nothing(self):
        self.assertFalse(self.app.record_lamp_state(False, at(9)))
        self.assertIsNone(self.app.clock_status(at(10)))

    def test_full_day_summary(self):
        self.app.record_lamp_state(True, at(9))
        self.app.record_lamp_state(False, at(10))
        self.app.record_lamp_state(True, at(11))
        summary = self.app.day_summary(DAY, at(12))
        self.assertEqual(summary.worked_seconds, 2 * 3600)
        self.assertEqual(summary.away_seconds, 3600)
        self.assertEqual(summary.session_count, 2)

    def test_rollover_across_two_midnights(self):
        self.app.record_lamp_state(True, at(21))
        day3 = DAY + dt.timedelta(days=2)
        self.app.record_lamp_state(False, at(1, day=day3))

        self.assertEqual(self.app.day_summary(DAY, at(9, day=day3)).worked_seconds, 3 * 3600)
        day2 = DAY + dt.timedelta(days=1)
        self.assertEqual(self.app.day_summary(day2, at(9, day=day3)).worked_seconds, 24 * 3600)
        self.assertEqual(self.app.day_summary(day3, at(9, day=day3)).worked_seconds, 3600)
        self.assertFalse(self.app.clock_status(at(9, day=day3)).is_clocked_in)

    def test_rollover_events_carry_rollover_provenance(self):
        self.app.record_lamp_state(True, at(21))
        day2 = DAY + dt.timedelta(days=1)
        self.app.advance_to(at(8, day=day2))
        notifications = self.app.notification_log.select(start=1, limit=10)
        events = [self.app.mapper.to_domain_event(n) for n in notifications]
        rollovers = [e for e in events if getattr(e, "provenance", None) is Provenance.ROLLOVER]
        self.assertEqual(len(rollovers), 2)

    def test_advance_to_keeps_open_session_current(self):
        self.app.record_lamp_state(True, at(21))
        day2 = DAY + dt.timedelta(days=1)
        self.app.advance_to(at(8, day=day2))
        status = self.app.clock_status(at(8, day=day2))
        self.assertTrue(status.is_clocked_in)
        self.assertEqual(status.since, at(0, day=day2))

    def test_advance_to_without_open_session_changes_nothing(self):
        self.app.record_lamp_state(True, at(9))
        self.app.record_lamp_state(False, at(10))
        day2 = DAY + dt.timedelta(days=1)
        self.app.advance_to(at(8, day=day2))
        self.assertIsNone(self.app.day_summary(day2, at(9, day=day2)))

    def test_strike_span_reduces_worked_time(self):
        self.app.record_lamp_state(True, at(9))
        self.app.record_lamp_state(False, at(12))
        self.app.strike_span(at(10), at(11), at(12, 30))
        summary = self.app.day_summary(DAY, at(13))
        self.assertEqual(summary.worked_seconds, 2 * 3600)
        self.assertEqual(summary.struck_seconds, 3600)

    def test_strike_session_covers_the_whole_session(self):
        self.app.record_lamp_state(True, at(9))
        self.app.record_lamp_state(False, at(10))
        self.app.record_lamp_state(True, at(11))
        self.app.strike_session(0, at(12))
        overviews = self.app.sessions(DAY, at(12))
        self.assertTrue(overviews[0].fully_struck)
        self.assertFalse(overviews[1].fully_struck)

    def test_strike_session_out_of_range_is_rejected(self):
        with self.assertRaises(NoSuchSession):
            self.app.strike_session(0, at(12))

    def test_events_survive_a_restart(self):
        env = {
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "TIMETRACKING_SQLITE_DBNAME": "file:shared?mode=memory&cache=shared",
        }
        first = TimeTracking(env=env)
        first.record_lamp_state(True, at(9), Provenance.RECONCILED)
        second = TimeTracking(env=env)
        reloaded = second.repository.get(WorkDay.create_id(DAY))
        self.assertEqual(reloaded.day, DAY)
        self.assertTrue(reloaded.is_clocked_in)


if __name__ == "__main__":
    unittest.main()
