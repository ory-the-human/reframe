"""The agent's update target and APK download. Run: python -m unittest"""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from reframe import agent, config
from reframe.frames import registry
from reframe.web import app


class AgentUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        self.apk = Path(self.tmp.name) / "agent.apk"
        self.apk.write_bytes(b"not really an apk")
        os.environ["AGENT_APK"] = str(self.apk)
        cfg = config.load()
        self.key = agent.ensure_key(cfg, registry.add(cfg, "f", "folder", folder=self.tmp.name))

    def tearDown(self):
        del os.environ["AGENT_APK"]
        self.tmp.cleanup()

    def test_config_names_the_apk_and_serves_it(self):
        client = app.test_client()
        auth = {"Authorization": f"Bearer {self.key}"}
        target = client.get("/agent/v1/config", headers=auth).get_json()["agent"]
        self.assertEqual(target["version"], "local")
        self.assertEqual(target["sha256"], hashlib.sha256(b"not really an apk").hexdigest())
        self.assertEqual(client.get(target["url"]).status_code, 401)
        resp = client.get(target["url"], headers=auth)
        self.assertEqual((resp.status_code, resp.data), (200, b"not really an apk"))
        resp.close()


if __name__ == "__main__":
    unittest.main()
