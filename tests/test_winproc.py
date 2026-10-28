import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import winproc


@unittest.skipIf(os.name == "nt", "o ~/.influencer.env só vale no macOS/Linux")
class LoadUserEnvTests(unittest.TestCase):
    def load(self, environ: dict[str, str]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".influencer.env"
            env_file.write_text("FFMPEG_EXECUTABLE=/opt/ffmpeg/bin/ffmpeg\nKOKORO_PYTHON=\"/opt/tts/bin/python\"\n"
                                "COMFYUI_URL=http://outro:8188\n# comentário\n", encoding="utf-8")
            with patch.object(winproc, "USER_ENV_FILE", env_file), patch.dict(os.environ, environ, clear=True):
                winproc.load_user_env()
                return dict(os.environ)

    def test_fills_missing_and_empty_values(self) -> None:
        env = self.load({"KOKORO_PYTHON": ""})
        self.assertEqual(env["FFMPEG_EXECUTABLE"], "/opt/ffmpeg/bin/ffmpeg")
        self.assertEqual(env["KOKORO_PYTHON"], "/opt/tts/bin/python")

    def test_path_replaces_bare_command_fallback(self) -> None:
        self.assertEqual(self.load({"FFMPEG_EXECUTABLE": "ffmpeg"})["FFMPEG_EXECUTABLE"], "/opt/ffmpeg/bin/ffmpeg")

    def test_keeps_values_already_defined(self) -> None:
        env = self.load({"FFMPEG_EXECUTABLE": "/usr/local/bin/ffmpeg", "COMFYUI_URL": "http://127.0.0.1:8188"})
        self.assertEqual(env["FFMPEG_EXECUTABLE"], "/usr/local/bin/ffmpeg")
        self.assertEqual(env["COMFYUI_URL"], "http://127.0.0.1:8188")


if __name__ == "__main__":
    unittest.main()
