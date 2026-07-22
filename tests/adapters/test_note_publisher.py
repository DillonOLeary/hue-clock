import datetime as dt
import unittest

from hue_clock.adapters.note_publisher import CapacitiesNotePublisher

DAY = dt.date(2026, 7, 21)


class FakeClient:
    def __init__(self, texts, findable=True):
        self.texts = texts
        self.findable = findable
        self.deleted = []
        self.appended = []

    def append_daily_note(self, line, date=None):
        self.appended.append((line, date))

    def search(self, query, structure_ids=None, limit=None):
        if not self.findable:
            return []
        return [{"id": "note-1", "title": "2026-07-21T00:00:00.000Z"}]

    def get_object(self, object_id):
        return {
            "blocks": {
                "root": [{"id": f"b{i}", "tokens": [{"text": t}]} for i, t in enumerate(self.texts)]
            }
        }

    def delete_block(self, object_id, block_id):
        self.deleted.append(block_id)


def publisher(client):
    return CapacitiesNotePublisher(client, pace=lambda seconds: None)


class AppendTest(unittest.TestCase):
    def test_append_targets_the_day(self):
        client = FakeClient([])
        publisher(client).append("🟢 9:00a", DAY)
        self.assertEqual(client.appended, [("🟢 9:00a", "2026-07-21")])


class LinePresentTest(unittest.TestCase):
    def test_present_when_text_matches(self):
        client = FakeClient(["🟢 9:00a"])
        self.assertTrue(publisher(client).line_present("🟢 9:00a", DAY))

    def test_absent_when_text_missing(self):
        client = FakeClient(["🔴 10:00a · 1h 00m"])
        self.assertFalse(publisher(client).line_present("🟢 9:00a", DAY))

    def test_absent_when_note_not_findable(self):
        client = FakeClient(["🟢 9:00a"], findable=False)
        self.assertFalse(publisher(client).line_present("🟢 9:00a", DAY))

    def test_unreadable_note_counts_as_present(self):
        client = FakeClient(["🟢 9:00a"])
        client.search = None
        self.assertTrue(publisher(client).line_present("🟢 9:00a", DAY))


class ScrubTest(unittest.TestCase):
    def scrub(self, texts):
        client = FakeClient(texts)
        removed = publisher(client).scrub_adjacent_duplicates(DAY)
        return removed, client.deleted

    def test_adjacent_duplicates_deleted_keeping_first(self):
        removed, deleted = self.scrub(
            [
                "🔴 12:50p · 11m",
                "🟢 2:44p · 1h 53m break",
                "🟢 2:44p · 1h 53m break",
                "🟢 2:44p · 1h 53m break",
            ]
        )
        self.assertEqual(removed, 2)
        self.assertEqual(deleted, ["b2", "b3"])

    def test_legitimate_repeat_across_alternation_kept(self):
        removed, _ = self.scrub(["🟢 2:45p", "🔴 2:45p · 0m", "🟢 2:45p"])
        self.assertEqual(removed, 0)

    def test_user_prose_ignored_and_never_deleted(self):
        removed, deleted = self.scrub(
            [
                "🟢 2:44p",
                "thinking about lunch",
                "thinking about lunch",
                "🟢 2:44p",
            ]
        )
        self.assertEqual(removed, 1)
        self.assertEqual(deleted, ["b3"])

    def test_unfindable_note_scrubs_nothing(self):
        client = FakeClient([], findable=False)
        self.assertEqual(publisher(client).scrub_adjacent_duplicates(DAY), 0)


if __name__ == "__main__":
    unittest.main()
