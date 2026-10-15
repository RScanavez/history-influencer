import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import comfyui, motion_jobs


class EnsureRunningTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        patches = [
            patch.object(comfyui, "comfyui_dir", return_value=self.folder),
            patch.object(comfyui.time, "sleep"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_already_up_does_not_launch(self):
        with patch.object(comfyui, "is_up", return_value=True), patch.object(comfyui, "launch") as launch:
            self.assertTrue(comfyui.ensure_running("http://x"))
        launch.assert_not_called()

    def test_down_launches_and_waits(self):
        with (
            patch.object(comfyui, "is_up", side_effect=[False, False, True]),
            patch.object(comfyui, "launch", return_value=4321) as launch,
            patch.object(motion_jobs, "_pid_alive", side_effect=lambda pid: pid == 4321),
        ):
            self.assertFalse(comfyui.ensure_running("http://x"))
        launch.assert_called_once()
        self.assertEqual((self.folder / "comfyui.pid").read_text(), "4321")

    def test_server_already_starting_is_not_launched_twice(self):
        (self.folder / "comfyui.pid").write_text("777")
        with (
            patch.object(comfyui, "is_up", side_effect=[False, True]),
            patch.object(comfyui, "launch") as launch,
            patch.object(motion_jobs, "_pid_alive", return_value=True),
        ):
            self.assertFalse(comfyui.ensure_running("http://x"))
        launch.assert_not_called()

    def test_process_dies_raises_quickly(self):
        alive = iter([False, False])  # pid anterior inexistente; o novo morre logo
        with (
            patch.object(comfyui, "is_up", return_value=False),
            patch.object(comfyui, "launch", return_value=99),
            patch.object(motion_jobs, "_pid_alive", side_effect=lambda pid: next(alive)),
        ):
            with self.assertRaisesRegex(RuntimeError, "encerrou logo após iniciar"):
                comfyui.ensure_running("http://x")


class DiscardOutputsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "output" / "sub").mkdir(parents=True)
        self.file = self.folder / "output" / "sub" / "clip-hd_00001_.png"
        self.file.write_bytes(b"x")
        patcher = patch.object(comfyui, "comfyui_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_local_copy_is_deleted_after_download(self):
        comfyui.discard_outputs("http://127.0.0.1:8188", [{"filename": self.file.name, "subfolder": "sub", "type": "output"}])
        self.assertFalse(self.file.exists())

    def test_remote_gpu_is_left_alone(self):
        comfyui.discard_outputs("http://127.0.0.1:8189", [{"filename": self.file.name, "subfolder": "sub", "type": "output"}])
        self.assertTrue(self.file.exists())


class GpuProfileTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(comfyui.os.environ, {"INFLUENCER_GPU_PROFILE": "", "COMFYUI_SPEED": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_profile_follows_gpu_name(self):
        self.assertEqual(comfyui.gpu_profile("NVIDIA GeForce RTX 3070 Laptop GPU")[0], "rtx3070")
        self.assertEqual(comfyui.gpu_profile("NVIDIA GeForce RTX 5070 Laptop GPU")[0], "rtx5070")
        self.assertEqual(comfyui.gpu_profile("NVIDIA GeForce RTX 4060")[0], "padrao")

    def test_env_forces_profile(self):
        with patch.dict(comfyui.os.environ, {"INFLUENCER_GPU_PROFILE": "rtx5070"}):
            self.assertEqual(comfyui.gpu_profile("NVIDIA GeForce RTX 3070 Laptop GPU")[0], "rtx5070")

    def test_3070_has_no_fp16_accumulation(self):
        # Com fp16_accumulation o ComfyUI multiplica o peso GGUF do Wan sem descompactar (2026-10-08).
        with patch.object(comfyui, "gpu_name", return_value="NVIDIA GeForce RTX 3070 Laptop GPU"):
            self.assertEqual(comfyui.speed_flags(Path("python.exe")), [])

    def test_5070_keeps_acceleration(self):
        with (
            patch.object(comfyui, "gpu_name", return_value="NVIDIA GeForce RTX 5070 Laptop GPU"),
            patch.object(comfyui.subprocess, "run") as run,
        ):
            run.return_value.returncode = 0
            self.assertEqual(comfyui.speed_flags(Path("python.exe")),
                             ["--fast", "fp16_accumulation", "--use-sage-attention"])


if __name__ == "__main__":
    unittest.main()
