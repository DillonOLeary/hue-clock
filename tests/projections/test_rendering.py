import datetime as dt
import unittest

from hue_clock.domain.ledger import DayLedger, TimeSpan
from hue_clock.projections.capacities_note.note_lines import (
    clock_in_line,
    clock_out_line,
    strike_line,
)


def at(hour, minute=0):
    return dt.datetime(2026, 7, 21, hour, minute)


class ClockInLineTest(unittest.TestCase):
    def test_first_clock_in_of_the_day(self):
        self.assertEqual(clock_in_line(DayLedger(), at(9, 12)), "🟢 9:12a")

    def test_break_shown_when_gap_is_a_minute_or_more(self):
        ledger = DayLedger().clock_in(at(9, 12)).clock_out(at(12, 40))
        self.assertEqual(clock_in_line(ledger, at(13, 32)), "🟢 1:32p · 52m break")

    def test_break_shown_at_exactly_one_minute(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(10, 0))
        self.assertEqual(clock_in_line(ledger, at(10, 1)), "🟢 10:01a · 1m break")

    def test_no_break_under_a_minute(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(10, 0))
        line = clock_in_line(ledger, at(10, 0) + dt.timedelta(seconds=45))
        self.assertEqual(line, "🟢 10:00a")

    def test_approx_marks_reconciled_transitions(self):
        self.assertEqual(clock_in_line(DayLedger(), at(9, 12), approx=True), "🟢 9:12a *(approx)*")


class ClockOutLineTest(unittest.TestCase):
    def test_first_clock_out_shows_session_length_only(self):
        ledger = DayLedger().clock_in(at(9, 12))
        self.assertEqual(clock_out_line(ledger, at(12, 40)), "🔴 12:40p · 3h 28m")

    def test_later_clock_outs_add_the_running_day_total(self):
        ledger = DayLedger().clock_in(at(9, 12)).clock_out(at(12, 40)).clock_in(at(13, 32))
        self.assertEqual(clock_out_line(ledger, at(17, 40)), "🔴 5:40p · 4h 08m · Σ 7h 36m")

    def test_day_total_subtracts_struck_time(self):
        ledger = (
            DayLedger()
            .clock_in(at(9))
            .clock_out(at(10))
            .clock_in(at(11))
            .strike(TimeSpan(at(9), at(9, 30)))
        )
        self.assertEqual(clock_out_line(ledger, at(12)), "🔴 12:00p · 1h 00m · Σ 1h 30m")


class StrikeLineTest(unittest.TestCase):
    def test_strike_window(self):
        self.assertEqual(strike_line(at(13, 32), at(14, 2)), "⚫ 1:32p–2:02p · −30m")


if __name__ == "__main__":
    unittest.main()
