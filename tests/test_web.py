"""The web UI's album picking on frame and album pages. Run: python -m unittest"""

import io
import os
import tempfile
import unittest

from PIL import Image

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

    def toggle(self, frame, album, on):
        return self.client.post(f"/frames/{frame.id}/albums", data={"album": album["id"], "show": ["no", "yes"] if on else ["no"]})

    def test_turning_an_album_off_on_an_every_album_frame_lists_the_others(self):
        every = registry.add(self.cfg, "every", "web")
        self.toggle(every, self.b, False)
        self.assertEqual(self.albums(every), [self.a["id"], self.c["id"]])
        self.toggle(every, self.b, True)
        self.assertEqual(self.albums(every), [self.a["id"], self.c["id"], self.b["id"]])

    def test_a_frames_only_album_cant_be_turned_off_and_every_clears_the_list(self):
        only = registry.add(self.cfg, "only", "web", albums=[self.b])
        self.toggle(only, self.b, False)
        self.assertEqual(self.albums(only), [self.b["id"]])  # empty would mean every album
        self.client.post(f"/frames/{only.id}/albums", data={"every": ["no", "yes"]})
        self.assertEqual(self.albums(only), [])

    def test_saving_frame_settings_keeps_its_albums(self):
        frame = registry.add(self.cfg, "f", "web", albums=[self.a])
        self.client.post(f"/frames/{frame.id}", data={"kind": "web", "name": "renamed"})
        self.assertEqual(self.albums(frame), [self.a["id"]])

    def test_frame_settings_save_only_what_the_form_carries_and_differs_from_the_default(self):
        frame = registry.add(self.cfg, "f", "web", display={"display_layout": "single"})
        self.client.post(f"/frames/{frame.id}", data={"display_interval": "20", "display_shuffle": ["no"], "display_date": ["no", "yes"]})
        saved = registry.find(registry.load_all(self.cfg), frame.id).display
        self.assertEqual(saved, {"display_layout": "single", "display_interval": "20", "display_shuffle": "no"})
        self.client.post(f"/frames/{frame.id}", data={"display_interval": "1"})
        self.assertEqual(registry.find(registry.load_all(self.cfg), frame.id).display["display_interval"], "20")

    def test_frame_settings_reach_the_slideshow_and_agent_unchanged(self):
        frame = registry.add(self.cfg, "f", "cozyla_adb", address="x:5555", display={"screen_schedule": "20:30-08:00", "display_sound": "yes"})
        self.assertEqual(registry.display_for(self.cfg, frame), {"interval": 15, "volume": 50, "layout": "pair", "orientation": "auto",
                                                                 "shuffle": True, "fill": "blur", "date": True, "sound": True})
        self.assertEqual(registry.screen_for(self.cfg, frame), {"schedule": {"off": "20:30", "on": "08:00"}, "dark": True, "dark_lux": 5, "dark_seconds": 30})
        self.assertTrue(registry.converts_videos(self.cfg, frame))

    def test_thumbnails_only_from_the_media_folders(self):
        folder = local.album_dir(self.cfg, self.a["id"])
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 400), "red").save(folder / "p.jpg")
        reply = self.client.get(f"/thumb/albums/{self.a['id']}/p.jpg")
        self.assertEqual((reply.status_code, reply.mimetype, Image.open(io.BytesIO(reply.data)).size), (200, "image/jpeg", (240, 160)))
        for rel in ("web_secret", f"albums/{self.a['id']}/../../web_secret"):
            self.assertEqual(self.client.get(f"/thumb/{rel}").status_code, 404, rel)

    def test_pages_render(self):
        frame = registry.add(self.cfg, "f", "web", albums=[self.a])
        adb = registry.add(self.cfg, "g", "cozyla_adb", address="x:5555")
        for path in ("/", "/settings", f"/frames/{frame.id}", f"/frames/{adb.id}", "/frames/new?kind=cozyla_adb", f"/albums/{self.a['id']}"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
