"""Local albums: listing and fetching from a folder on the server. Run: python -m unittest"""

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from reframe import config
from reframe.sources import Album, local


class LocalAlbumTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        self.cfg = config.load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_and_fetches_a_resized_upright_jpeg(self):
        a = local.add(self.cfg, "Holidays")
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: rotate 90° to show it upright
        exif.get_ifd(0x8769).update({36867: "2024:05:01 12:34:56", 36881: "+02:00"})
        Image.new("RGB", (4000, 3000), "red").save(local.album_dir(self.cfg, a["id"]) / "IMG_1.JPG", exif=exif)

        listing = local.LocalSource(self.cfg).list_album(Album(a["id"], a["name"]))
        self.assertTrue(listing.complete and listing.trusted)
        [item] = listing.items
        self.assertEqual((item.mime_type, item.created), ("image/jpeg", "2024-05-01T12:34:56+02:00"))
        self.assertEqual(local.LocalSource(self.cfg).list_album(listing.album).items[0].id, item.id)

        dest = Path(self.tmp.name) / "out.jpg"
        local.LocalSource(self.cfg).fetch(item, dest, 1000)
        with Image.open(dest) as img:
            self.assertEqual((img.format, img.size), ("JPEG", (750, 1000)))


if __name__ == "__main__":
    unittest.main()
