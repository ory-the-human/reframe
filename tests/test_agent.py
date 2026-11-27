"""The agent endpoints for the local slideshow, settings set on the frame, screenshots and reboots. Run: python -m unittest"""

import os
import tempfile
import unittest

from reframe import agent, config, web
from reframe.frames import registry


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        os.environ.pop("WEB_PASSWORD", None)
        self.cfg = config.load()
        frame = registry.add(self.cfg, "k", "cozyla_adb", address="10.0.0.5:5555")
        frame.key = "showkey"
        frame.kiosk = {"provisioned_at": 1, "start_url": "http://server:1992/show/showkey"}
        registry.update(self.cfg, frame)
        self.frame = frame
        self.auth = {"Authorization": "Bearer " + agent.ensure_key(self.cfg, frame)}
        web.app.secret_key = b"test"
        self.client = web.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, body=None):
        return self.client.post("/agent/v1/report", json=body or {}, headers=self.auth).get_json()

    def test_slideshow_needs_the_key_and_lists_the_files(self):
        media = registry.web_media_dir(self.cfg.data_dir, self.frame.id)
        media.mkdir(parents=True)
        (media / "a.jpg").write_bytes(b"jpg")
        (media / "b.mp4").write_bytes(b"mp4")
        self.assertEqual(self.client.get("/agent/v1/slideshow").status_code, 401)
        self.assertEqual(self.client.get("/agent/v1/slideshow", headers={"Authorization": "Bearer nope"}).status_code, 401)
        body = self.client.get("/agent/v1/slideshow", headers=self.auth).get_json()
        self.assertEqual([(i["name"], i["type"]) for i in body["items"]], [("a.jpg", "image"), ("b.mp4", "video")])
        self.assertIn("/show/showkey", body["page"])
        self.assertEqual(self.client.get("/agent/v1/media/b.mp4", headers=self.auth).data, b"mp4")

    def test_home_url_switches_once_the_agent_serves_locally(self):
        def kiosk():
            return self.client.get("/agent/v1/config", headers=self.auth).get_json()

        before = kiosk()
        self.assertEqual(before["kiosk"]["home_url"], "http://server:1992/show/showkey")
        self.report({"agent_version": "0.2.0"})
        self.assertEqual(kiosk()["version"], before["version"])
        reply = self.report({"local_port": 1992})
        after = kiosk()
        self.assertEqual(after["kiosk"]["home_url"], "http://127.0.0.1:1992/show/showkey")
        self.assertEqual(after["kiosk"]["settings"]["web_content.home_url"], "http://127.0.0.1:1992/show/showkey")
        self.assertNotEqual(after["version"], before["version"])
        self.assertEqual(reply["config_version"], after["version"])

    def test_screenshot_is_flagged_once_and_stored(self):
        self.client.post(f"/frames/{self.frame.id}/screenshot")
        self.assertTrue(self.report().get("screenshot"))
        self.assertNotIn("screenshot", self.report())
        self.client.post("/agent/v1/screenshot", data=b"png", headers=self.auth)
        self.assertEqual(self.client.get(f"/frames/{self.frame.id}/screenshot.png").data, b"png")

    def test_reboot_is_flagged_once(self):
        self.client.post(f"/frames/{self.frame.id}/reboot")
        self.assertTrue(self.report().get("reboot"))
        self.assertNotIn("reboot", self.report())

    def test_settings_from_the_frame_are_saved(self):
        change = {"interval": 20, "sound": True, "volume": 60}
        self.assertEqual(self.client.post("/agent/v1/display", json=change).status_code, 401)
        self.client.post("/agent/v1/display", json=change, headers=self.auth)
        saved = registry.find(registry.load_all(self.cfg), self.frame.id).display
        self.assertEqual((saved["display_interval"], saved["display_sound"], saved["display_volume"]), ("20", "yes", "60"))

    def test_slideshow_carries_the_frames_settings(self):
        self.client.post("/agent/v1/display", json={"interval": 20, "sound": True, "volume": 60}, headers=self.auth)
        display = self.client.get("/agent/v1/slideshow", headers=self.auth).get_json()["display"]
        self.assertEqual((display["interval"], display["sound"], display["volume"]), (20, True, 60))


if __name__ == "__main__":
    unittest.main()
