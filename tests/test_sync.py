"""The sync engine against a fake source and folder frames. Run: python -m unittest"""

import os
import tempfile
import unittest
from pathlib import Path

from reframe import config, sync
from reframe.frames import registry
from reframe.sources import Album, Listing, MediaItem


class FakeSource:
    kind = "fake"

    def __init__(self):
        self.content: dict[str, list[str]] = {}  # album id -> item ids
        self.complete = True
        self.fail: set[str] = set()
        self.created: dict[str, str] = {}

    def albums(self):
        return [Album(a, a.upper()) for a in self.content]

    def list_album(self, album):
        items = [MediaItem(id=i, mime_type="image/jpeg", created=self.created.get(i)) for i in self.content[album.id]]
        return Listing(album=album, items=items, complete=self.complete)

    def fetch(self, item, dest, max_px):
        if item.id in self.fail:
            raise RuntimeError("download failed")
        dest.write_text(item.id)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        self.cfg = config.load()
        self.source = FakeSource()

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, name, albums=()):
        folder = Path(self.tmp.name) / name
        return registry.add(self.cfg, name, "folder", folder=str(folder), albums=[{"id": a, "name": a} for a in albums])

    def files(self, name):
        folder = Path(self.tmp.name) / name
        return sorted(p.stem for p in folder.glob("*.jpg")) if folder.exists() else []

    def sync(self, **kw):
        return sync.run(self.cfg, self.source, **kw)

    def test_records_and_fills_in_taken_dates(self):
        f = self.frame("f")
        self.source.content = {"a": ["1"]}
        self.sync()
        self.assertEqual(sync.taken(self.cfg, f.id), {})
        self.source.content["a"].append("2")
        self.source.created = {"1": "2023-11-14T22:13:20+00:00", "2": "2024-01-02T03:04:05+00:00"}
        self.sync()
        self.assertEqual(sync.taken(self.cfg, f.id), {"1.jpg": "2023-11-14T22:13:20+00:00", "2.jpg": "2024-01-02T03:04:05+00:00"})

    def test_adds_then_removes_after_grace(self):
        self.frame("f")
        self.source.content = {"a": ["1", "2", "3", "4", "5"]}
        self.sync()
        self.assertEqual(self.files("f"), ["1", "2", "3", "4", "5"])
        self.source.content["a"].remove("5")
        self.sync()
        self.assertIn("5", self.files("f"))  # missing once: kept
        self.sync()
        self.assertNotIn("5", self.files("f"))

    def test_back_in_time_resets_the_count(self):
        self.frame("f")
        self.source.content = {"a": ["1", "2", "3", "4", "5"]}
        self.sync()
        self.source.content["a"].remove("5")
        self.sync()
        self.source.content["a"].append("5")
        self.sync()
        self.source.content["a"].remove("5")
        self.sync()
        self.assertIn("5", self.files("f"))

    def test_incomplete_listing_removes_nothing(self):
        self.frame("f")
        self.source.content = {"a": ["1", "2", "3", "4", "5"]}
        self.sync()
        self.source.content["a"] = ["1", "2", "3", "4"]
        self.source.complete = False
        for _ in range(4):
            self.sync()
        self.assertIn("5", self.files("f"))

    def test_empty_listing_is_doubted(self):
        self.frame("f")
        self.source.content = {"a": ["1", "2"]}
        self.sync()
        self.source.content["a"] = []
        for _ in range(4):
            self.sync()
        self.assertEqual(self.files("f"), ["1", "2"])

    def test_mass_removal_waits_longer(self):
        self.frame("f")
        self.source.content = {"a": [str(i) for i in range(10)]}
        self.sync()
        self.source.content["a"] = ["0", "1", "2", "3", "4"]
        for _ in range(sync.REMOVE_AFTER_RUNS + sync.MASS_EXTRA_RUNS - 1):
            self.sync()
            self.assertEqual(len(self.files("f")), 10)
        self.sync()
        self.assertEqual(len(self.files("f")), 5)

    def test_deselected_album_goes_at_once(self):
        f = self.frame("f", albums=["a", "b"])
        self.source.content = {"a": ["1"], "b": ["2"]}
        self.sync()
        f.albums = [{"id": "a", "name": "a"}]
        registry.update(self.cfg, f)
        self.sync()
        self.assertEqual(self.files("f"), ["1"])

    def test_shared_item_keeps_its_file_while_used(self):
        f = self.frame("f", albums=["a", "b"])
        self.source.content = {"a": ["1", "s"], "b": ["2", "s"]}
        self.sync()
        self.assertEqual(self.files("f"), ["1", "2", "s"])
        f.albums = [{"id": "b", "name": "b"}]
        registry.update(self.cfg, f)
        self.sync()
        self.assertEqual(self.files("f"), ["2", "s"])

    def test_frames_get_their_own_albums(self):
        self.frame("x", albums=["a"])
        self.frame("y")
        self.source.content = {"a": ["1"], "b": ["2"]}
        self.sync()
        self.assertEqual(self.files("x"), ["1"])
        self.assertEqual(self.files("y"), ["1", "2"])

    def test_failed_download_is_retried(self):
        self.frame("f")
        self.source.content = {"a": ["1", "2"]}
        self.source.fail = {"2"}
        summary = self.sync()
        self.assertEqual(summary["frames"][0]["albums"][0]["failed"], 1)
        self.assertEqual(self.files("f"), ["1"])
        self.source.fail = set()
        self.sync()
        self.assertEqual(self.files("f"), ["1", "2"])

    def test_dry_run_changes_nothing(self):
        self.frame("f")
        self.source.content = {"a": ["1"]}
        summary = self.sync(dry_run=True)
        self.assertEqual(summary["frames"][0]["albums"][0]["added"], 1)
        self.assertEqual(self.files("f"), [])
        self.assertIsNone(sync.last_run(self.cfg))
        self.sync()
        self.assertEqual(self.files("f"), ["1"])

    def test_one_sync_at_a_time(self):
        self.frame("f")
        self.source.content = {"a": ["1"]}
        with sync._exclusive(self.cfg):
            with self.assertRaises(sync.SyncBusy):
                self.sync()


if __name__ == "__main__":
    unittest.main()
