import contextlib
import datetime as dt
import io
import unittest

from eventsourcing.system import SingleThreadedRunner, System

from hue_clock.application.time_tracking import TimeTracking
from hue_clock.projections.capacities_note.flusher import (
    RESEND_GRACE_SECONDS,
    NoteFlusher,
)
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0, day=DAY):
    return dt.datetime.combine(day, dt.time(hour, minute))


class FakePublisher:
    def __init__(self, present=False):
        self.appended = []
        self.scrubbed = []
        self.present = present
        self.fail_append = False
        self.scrub_result = 3

    def append(self, line, day):
        if self.fail_append:
            raise RuntimeError("HTTP 502")
        self.appended.append((day, line))

    def line_present(self, line, day):
        return self.present

    def scrub_adjacent_duplicates(self, day):
        self.scrubbed.append(day)
        return self.scrub_result


class Clock:
    def __init__(self, start):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += dt.timedelta(seconds=seconds)


class FlusherTest(unittest.TestCase):
    def setUp(self):
        system = System(pipes=[[TimeTracking, CapacitiesNoteProjection]])
        self.runner = SingleThreadedRunner(system)
        self.runner.start()
        self.addCleanup(self.runner.stop)
        self.tracking = self.runner.get(TimeTracking)
        self.notes = self.runner.get(CapacitiesNoteProjection)
        self.publisher = FakePublisher()
        self.clock = Clock(at(18))
        self.settled = []
        self.flusher = NoteFlusher(
            self.notes, self.publisher, now=self.clock, settle=self.settled.append
        )

    def record(self, *transitions):
        with contextlib.redirect_stdout(io.StringIO()):
            for on, when in transitions:
                self.tracking.record_lamp_state(on, when)

    def flush(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.flusher.flush()

    def queued_texts(self, day=DAY):
        note = self.notes.note(day)
        return [line.text for line in note.queue] if note else []

    def test_send_and_confirm_drains_the_queue(self):
        self.publisher.present = True
        self.record((True, at(9)))
        self.flush()
        self.assertEqual(self.publisher.appended, [(DAY, "🟢 9:00a")])
        self.assertEqual(self.queued_texts(), [])
        self.assertEqual(self.notes.note(DAY).last_confirmed_at, at(18))
        self.assertEqual(self.publisher.scrubbed, [])

    def test_unconfirmed_line_is_not_resent(self):
        self.record((True, at(9)))
        self.flush()
        self.flush()
        self.assertEqual(len(self.publisher.appended), 1)
        head = self.notes.note(DAY).head
        self.assertTrue(head.is_sent)
        self.assertEqual(head.resends, 0)

    def test_late_confirm_drains_without_resend_or_scrub(self):
        self.record((True, at(9)))
        self.flush()
        self.publisher.present = True
        self.flush()
        self.assertEqual(len(self.publisher.appended), 1)
        self.assertEqual(self.queued_texts(), [])
        self.assertEqual(self.publisher.scrubbed, [])

    def test_resend_after_grace(self):
        self.record((True, at(9)))
        self.flush()
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.flush()
        self.assertEqual(len(self.publisher.appended), 2)
        self.assertEqual(self.notes.note(DAY).head.resends, 1)

    def test_no_resend_before_grace(self):
        self.record((True, at(9)))
        self.flush()
        self.clock.advance(60)
        self.flush()
        self.assertEqual(len(self.publisher.appended), 1)

    def test_grace_doubles_per_resend(self):
        self.record((True, at(9)))
        self.flush()
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.flush()
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.flush()
        self.assertEqual(len(self.publisher.appended), 2)
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.flush()
        self.assertEqual(len(self.publisher.appended), 3)

    def test_resent_line_confirming_triggers_scrub(self):
        self.record((True, at(9)))
        self.flush()
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.publisher.present = True

        def present_after_resend(line, day):
            return len(self.publisher.appended) > 1

        self.publisher.line_present = present_after_resend
        self.flush()
        self.assertEqual(self.queued_texts(), [])
        self.assertEqual(self.publisher.scrubbed, [DAY])

    def test_append_failure_keeps_line_unsent(self):
        self.publisher.fail_append = True
        self.record((True, at(9)))
        self.flush()
        head = self.notes.note(DAY).head
        self.assertFalse(head.is_sent)
        self.assertEqual(self.queued_texts(), ["🟢 9:00a"])

    def test_head_blocks_later_lines_within_a_day(self):
        self.record((True, at(9)), (False, at(10)))
        self.flush()
        self.assertEqual(self.publisher.appended, [(DAY, "🟢 9:00a")])

    def test_wedged_day_does_not_block_other_days(self):
        day2 = DAY + dt.timedelta(days=1)
        self.record((True, at(9)), (False, at(10)))
        self.flush()

        self.record((True, at(9, day=day2)))
        self.clock.current = at(18, day=day2)

        def confirmed_day2(line, day):
            return day == day2
        self.publisher.line_present = confirmed_day2
        self.flush()

        self.assertIn((day2, "🟢 9:00a"), self.publisher.appended)
        self.assertEqual(self.queued_texts(day2), [])
        self.assertEqual(len(self.queued_texts(DAY)), 2)

    def test_scrub_failure_still_confirms(self):
        self.record((True, at(9)))
        self.flush()
        self.clock.advance(RESEND_GRACE_SECONDS + 60)
        self.publisher.line_present = lambda line, day: len(self.publisher.appended) > 1

        def broken_scrub(day):
            raise RuntimeError("rate limited")

        self.publisher.scrub_adjacent_duplicates = broken_scrub
        self.flush()
        self.assertEqual(self.queued_texts(), [])


if __name__ == "__main__":
    unittest.main()
