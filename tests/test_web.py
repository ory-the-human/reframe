"""The web UI's album picking on frame and album pages. Run: python -m unittest"""

import os
import tempfile
import unittest

from reframe import config, web
from reframe.frames import registry
from reframe.sources import local


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        os.environ.pop("WEB_PASSWORD", None)
        self.cfg = config.load()
        self.a, self.b, self.c = (local.add(self.cfg, n) for n in "ABC")
        web.app.secret_key = b"test"
        self.client = web.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def albums(self, frame):
        return [a["id"] for a in registry.find(registry.load_all(self.cfg), frame.id).albums]

    def test_album_page_lists_the_other_albums_when_unticking_an_all_albums_frame(self):
        every = registry.add(self.cfg, "every", "web")
        some = registry.add(self.cfg, "some", "web", albums=[self.a])
        only = registry.add(self.cfg, "only", "web", albums=[self.b])
        self.client.post(f"/albums/{self.b['id']}/frames", data={"frame": [some.id]})
        self.assertEqual(self.albums(every), [self.a["id"], self.c["id"]])
        self.assertEqual(self.albums(some), [self.a["id"], self.b["id"]])
        self.assertEqual(self.albums(only), [self.b["id"]])  # empty would mean every album

    def test_frame_page_adds_from_all_albums_and_removing_the_last_shows_all_again(self):
        frame = registry.add(self.cfg, "f", "web")
        self.client.post(f"/frames/{frame.id}/albums", data={"add": self.a["id"]})
        self.assertEqual(self.albums(frame), [self.a["id"]])
        self.client.post(f"/frames/{frame.id}/albums", data={"remove": self.a["id"]})
        self.assertEqual(self.albums(frame), [])

    def test_saving_frame_settings_keeps_its_albums(self):
        frame = registry.add(self.cfg, "f", "web", albums=[self.a])
        self.client.post(f"/frames/{frame.id}", data={"kind": "web", "name": "renamed"})
        self.assertEqual(self.albums(frame), [self.a["id"]])

    def test_pages_render(self):
        frame = registry.add(self.cfg, "f", "web", albums=[self.a])
        for path in ("/", "/settings", f"/frames/{frame.id}", "/frames/new", f"/albums/{self.a['id']}"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
