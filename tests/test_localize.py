import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import localize


class LocalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Estes testes tratam FR/EN como ativos, independente da flag "idiomas" do projeto.
        patcher = patch.object(localize, "enabled_languages", return_value=["fr", "en"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fitted_speed_speeds_up_long_speech_and_clamps(self) -> None:
        self.assertEqual(localize.fitted_speed(9.8, 10.0), 1.0)
        self.assertEqual(localize.fitted_speed(11.0, 10.0), 1.122)
        self.assertEqual(localize.fitted_speed(20.0, 10.0), localize.MAX_SPEED)
        self.assertEqual(localize.fitted_speed(9.4, 10.0), 0.94)
        self.assertEqual(localize.fitted_speed(5.0, 10.0), localize.MIN_SPEED)

    def test_segment_scenes_follow_manifest_and_skip_removed_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video_dir = Path(temporary)
            (video_dir / "segments.txt").write_text(
                "".join(f"file 'D:/x/scene-{n:02d}/segment.mp4'\n" for n in (1, 3, 4)), encoding="utf-8"
            )
            self.assertEqual(localize.segment_scenes(video_dir), [1, 3, 4])

    def test_save_translation_validates_and_waits_for_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            video_dir = Path(temporary)
            with self.assertRaises(ValueError):
                localize.save_translation(video_dir, {"language": "fr", "title": "T", "description": "D", "scenes": []})
            saved = localize.save_translation(video_dir, {
                "language": "fr", "title": "Titre", "description": "Desc",
                "scenes": [{"scene": 1, "narration": "Bonjour."}],
            })
            self.assertEqual(saved.name, "translation-fr.json")
            self.assertIsNone(localize.localize_if_ready(video_dir))

    def test_english_version_is_american_with_own_suffix_and_only_requested_language_is_built(self) -> None:
        from unittest.mock import patch

        self.assertEqual(localize.LANGUAGES["en"]["kokoro"], "a")
        self.assertEqual(localize.LANGUAGES["en"]["suffix"], "_ENG")
        with tempfile.TemporaryDirectory() as temporary:
            video_dir = Path(temporary)
            (video_dir / "short.mp4").write_bytes(b"")
            for language in ("fr", "en"):
                localize.save_translation(video_dir, {
                    "language": language, "title": "T", "description": "D",
                    "scenes": [{"scene": 1, "narration": "Hi."}],
                })
            with patch.object(localize, "build_localized_short", side_effect=lambda d, t, progress=None: t["language"]) as build:
                self.assertEqual(localize.localize_if_ready(video_dir, languages=["en"]), {"en": "en"})
            build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
