import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_rules import kind_of, restricted_reason, safe_archive_path


class MediaClassificationTests(unittest.TestCase):
    def msg(self, photo=None, video=None, document=None, media=None, noforwards=False):
        return SimpleNamespace(photo=photo, video=video, document=document, media=media, noforwards=noforwards)

    def test_photo_and_video(self):
        self.assertEqual(kind_of(self.msg(photo=object())), "photo")
        self.assertEqual(kind_of(self.msg(video=object())), "video")

    def test_other_documents_saved(self):
        self.assertEqual(kind_of(self.msg(document=SimpleNamespace(mime_type="application/pdf"))), "document")

    def test_view_once_is_restricted(self):
        self.assertIsNotNone(restricted_reason(self.msg(media=SimpleNamespace(ttl_seconds=0x7FFFFFFF))))
        self.assertIsNotNone(restricted_reason(self.msg(media=SimpleNamespace(ttl_seconds=5))))

    def test_autodelete_regular_media_is_accessible(self):
        self.assertIsNone(restricted_reason(self.msg(media=SimpleNamespace(ttl_seconds=None))))

    def test_protected_content_is_restricted(self):
        self.assertIsNotNone(restricted_reason(self.msg(noforwards=True)))

    def test_path_traversal_protected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "test.jpg").write_bytes(b"hi")
            self.assertEqual(safe_archive_path(root, "test.jpg"), root / "test.jpg")
            self.assertIsNone(safe_archive_path(root, "../other"))


if __name__ == "__main__":
    unittest.main()
