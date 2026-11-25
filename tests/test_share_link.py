"""Parsing share-link album pages, on synthetic data shaped like Google's reply."""

import unittest
from unittest import mock

from reframe.sources import Album
from reframe.sources.share_link import ShareLinkSource


def item(id, extra=None):
    return [id, [f"https://lh3.example/{id}", 3024, 4032, None, None, None, None, None, [None, None, 14], [100]],
            1700000000000, "x", -21600000, 1700000001000, ["y"], [[2]], 2, {"15": 1, **(extra or {})}]


def page(items, count):
    details = ["album1", "Test album"] + [None] * 19 + [count]
    return [None, items, "", details, None, 0]


class ShareLinkTest(unittest.TestCase):
    def listing(self, pg):
        with mock.patch("reframe.sources.share_link._page", return_value=pg), \
                mock.patch.object(ShareLinkSource, "_key", return_value="k"):
            return ShareLinkSource(None).list_album(Album("album1", "Test album"))

    def test_photos_live_photo_and_video(self):
        raws = [item("p1"), item("live", {"146008172": [None, 1700]}), item("v1", {"76647426": [5000]})]
        listing = self.listing(page(raws, 3))
        self.assertTrue(listing.complete)
        self.assertEqual([(i.id, i.mime_type) for i in listing.items],
                         [("p1", "image/jpeg"), ("live", "image/jpeg"), ("v1", "video/mp4")])
        self.assertEqual((listing.items[0].width, listing.items[0].height), (3024, 4032))
        self.assertEqual(listing.items[0].created, "2023-11-14T16:13:20-06:00")
        self.assertEqual(listing.items[0].extra["url"], "https://lh3.example/p1")

    def test_unknown_item_shape_is_incomplete(self):
        self.assertFalse(self.listing(page([item("p1"), {"new": "shape"}], 2)).complete)

    def test_count_mismatch_is_incomplete(self):
        self.assertFalse(self.listing(page([item("p1")], 2)).complete)


if __name__ == "__main__":
    unittest.main()
