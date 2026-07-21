import datetime as dt
import unittest

from hue_clock.domain.ledger import DayLedger, Session, TimeSpan, merge_spans, overlap_seconds


def at(hour, minute=0):
    return dt.datetime(2026, 7, 21, hour, minute)


def span(start_hour, end_hour):
    return TimeSpan(at(start_hour), at(end_hour))


class MergeSpansTest(unittest.TestCase):
    def test_overlapping_spans_merge(self):
        self.assertEqual(merge_spans([span(9, 11), span(10, 12)]), (span(9, 12),))

    def test_adjacent_spans_merge(self):
        self.assertEqual(merge_spans([span(9, 10), span(10, 11)]), (span(9, 11),))

    def test_disjoint_spans_stay_separate(self):
        self.assertEqual(merge_spans([span(12, 13), span(9, 10)]), (span(9, 10), span(12, 13)))

    def test_contained_span_disappears(self):
        self.assertEqual(merge_spans([span(9, 15), span(10, 11)]), (span(9, 15),))


class OverlapSecondsTest(unittest.TestCase):
    def test_sums_pairwise_overlap(self):
        worked = [span(9, 12), span(13, 17)]
        struck = [span(11, 14)]
        self.assertEqual(overlap_seconds(worked, struck), 2 * 3600)

    def test_no_overlap_is_zero(self):
        self.assertEqual(overlap_seconds([span(9, 10)], [span(11, 12)]), 0)


class DayLedgerTest(unittest.TestCase):
    def test_clock_in_opens_a_session(self):
        ledger = DayLedger().clock_in(at(9))
        self.assertEqual(ledger.open_session, Session(at(9)))
        self.assertTrue(ledger.is_clocked_in)

    def test_clock_out_closes_the_open_session(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(12))
        self.assertIsNone(ledger.open_session)
        self.assertEqual(ledger.closed_sessions, (Session(at(9), at(12)),))

    def test_worked_seconds_includes_open_session_up_to_now(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(10)).clock_in(at(11))
        self.assertEqual(ledger.worked_seconds(at(11, 30)), 1.5 * 3600)

    def test_away_seconds_sums_gaps_between_sessions(self):
        ledger = (
            DayLedger()
            .clock_in(at(9)).clock_out(at(10))
            .clock_in(at(11)).clock_out(at(12))
            .clock_in(at(14))
        )
        self.assertEqual(ledger.away_seconds(), 3 * 3600)

    def test_gap_before_measures_from_last_close(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(10))
        self.assertEqual(ledger.gap_before(at(10, 30)), dt.timedelta(minutes=30))

    def test_gap_before_is_none_for_first_session(self):
        self.assertIsNone(DayLedger().gap_before(at(9)))

    def test_strike_subtracts_only_overlap_with_sessions(self):
        ledger = (
            DayLedger()
            .clock_in(at(9)).clock_out(at(10))
            .clock_in(at(11)).clock_out(at(12))
            .strike(span(9, 12))
        )
        self.assertEqual(ledger.worked_seconds(at(13)), 0)
        self.assertEqual(ledger.struck_seconds(at(13)), 2 * 3600)

    def test_overlapping_strikes_never_double_count(self):
        ledger = (
            DayLedger()
            .clock_in(at(9)).clock_out(at(12))
            .strike(span(9, 11)).strike(span(10, 12))
        )
        self.assertEqual(ledger.struck_seconds(at(13)), 3 * 3600)
        self.assertEqual(ledger.worked_seconds(at(13)), 0)

    def test_closed_total_ignores_open_session(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(10)).clock_in(at(11))
        self.assertEqual(ledger.closed_total_seconds(), 3600)

    def test_closed_total_subtracts_struck_overlap(self):
        ledger = (
            DayLedger()
            .clock_in(at(9)).clock_out(at(11))
            .strike(span(10, 11))
        )
        self.assertEqual(ledger.closed_total_seconds(), 3600)

    def test_struck_coverage_of_a_single_session(self):
        ledger = DayLedger().clock_in(at(9)).clock_out(at(11)).strike(span(9, 10))
        session = ledger.closed_sessions[0]
        self.assertEqual(ledger.struck_coverage(session, at(12)), 3600)


if __name__ == "__main__":
    unittest.main()
