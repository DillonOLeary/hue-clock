import contextlib
import datetime as dt
import io
import unittest

from eventsourcing.system import SingleThreadedRunner, System

from hue_clock.application.time_tracking import TimeTracking
from hue_clock.projections.capacities_note.flusher import NoteFlusher
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection

DAY = dt.date(2026, 7, 21)


def at(hour, minute=0, day=DAY):
    return dt.datetime.combine(day, dt.time(hour, minute))


class FakePublisher:
    def __init__(self):
        self.appended = []
        self.fail_append = False

    def append(self, line, day):
        if self.fail_append:
            raise RuntimeError("HTTP 502")
        self.appended.append((day, line))


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
        self.flusher = NoteFlusher(self.notes, self.publisher, now=self.clock)

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

    def test_append_pops_the_line_on_success(self):
        self.record((True, at(9)))
        self.flush()
        self.assertEqual(self.publisher.appended, [(DAY, "🟢 9:00a")])
        self.assertEqual(self.queued_texts(), [])
        self.assertEqual(self.notes.note(DAY).last_confirmed_at, at(18))

    def test_append_failure_keeps_line_queued(self):
        self.publisher.fail_append = True
        self.record((True, at(9)))
        self.flush()
        self.assertEqual(self.queued_texts(), ["🟢 9:00a"])
        # A later pass, once appends work again, drains it with no duplicate.
        self.publisher.fail_append = False
        self.flush()
        self.assertEqual(self.publisher.appended, [(DAY, "🟢 9:00a")])
        self.assertEqual(self.queued_texts(), [])

    def test_already_sent_head_drains_without_reappend(self):
        # Migration guard: a line carrying sent_at (a 200 from the read-back
        # era) is popped without being appended again.
        self.record((True, at(9)))
        with contextlib.redirect_stdout(io.StringIO()):
            note = self.notes.note(DAY)
            note.head_sent(at(9, 30))
            self.notes.save(note)
        self.flush()
        self.assertEqual(self.publisher.appended, [])
        self.assertEqual(self.queued_texts(), [])
        self.assertEqual(self.notes.note(DAY).last_confirmed_at, at(18))

    def test_head_blocks_later_lines_within_a_day(self):
        self.publisher.fail_append = True
        self.record((True, at(9)), (False, at(10)))
        self.flush()
        # First line never lands, so the second stays behind it.
        self.assertEqual(self.publisher.appended, [])
        self.assertEqual(self.queued_texts(), ["🟢 9:00a", "🔴 10:00a · 1h 00m"])

    def test_wedged_day_does_not_block_other_days(self):
        day2 = DAY + dt.timedelta(days=1)
        self.record((True, at(9)), (False, at(10)))

        original_append = self.publisher.append

        def fail_only_day1(line, day):
            if day == DAY:
                raise RuntimeError("HTTP 502")
            original_append(line, day)

        self.publisher.append = fail_only_day1
        self.record((True, at(9, day=day2)))
        self.clock.current = at(18, day=day2)
        self.flush()

        self.assertIn((day2, "🟢 9:00a"), self.publisher.appended)
        self.assertEqual(self.queued_texts(day2), [])
        self.assertEqual(len(self.queued_texts(DAY)), 2)


if __name__ == "__main__":
    unittest.main()
