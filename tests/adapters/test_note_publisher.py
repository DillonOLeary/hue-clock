import datetime as dt
import unittest

from hue_clock.adapters.note_publisher import CapacitiesNotePublisher

DAY = dt.date(2026, 7, 21)


class FakeClient:
    def __init__(self):
        self.appended = []

    def append_daily_note(self, line, date=None):
        self.appended.append((line, date))


class AppendTest(unittest.TestCase):
    def test_append_targets_the_day(self):
        client = FakeClient()
        CapacitiesNotePublisher(client).append("🟢 9:00a", DAY)
        self.assertEqual(client.appended, [("🟢 9:00a", "2026-07-21")])


if __name__ == "__main__":
    unittest.main()
