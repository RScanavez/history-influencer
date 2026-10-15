import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from influencer_mcp import gpu_auxiliar, motion

# Saída real do rife-ncnn-vulkan no ASUS TUF A16 (2026-10-11), mais uma placa de software que deve ser ignorada.
LOG = """[0 AMD Radeon 780M Graphics]  queueC=1[8]  queueG=0[8]  queueT=2[1]
[0 AMD Radeon 780M Graphics]  bugsbn1=0  bugbilz=0  bugcopc=0  bugihfa=0
[1 NVIDIA GeForce RTX 5070 Laptop GPU]  queueC=2[8]  queueG=0[16]  queueT=1[2]
[2 llvmpipe (LLVM 15.0.7, 256 bits)]  queueC=0[1]  queueG=0[1]  queueT=0[1]
"""
AUX = gpu_auxiliar.Auxiliary(Path("rife.exe"), Path("rife-v4.6"), gpu_auxiliar.Device(0, "AMD Radeon 780M Graphics"),
                             gpu_auxiliar.Device(1, "NVIDIA GeForce RTX 5070 Laptop GPU"))


class DetectionTests(unittest.TestCase):
    def test_two_gpus_pick_the_non_nvidia_one_and_keep_nvidia_as_fallback(self):
        devices = gpu_auxiliar.parse_devices(LOG)
        self.assertEqual([device.index for device in devices], [0, 1])
        aux, fallback = gpu_auxiliar.choose(devices)
        self.assertEqual((aux.index, fallback.index), (0, 1))

    def test_single_gpu_or_two_nvidia_change_nothing(self):
        nvidia = gpu_auxiliar.Device(0, "NVIDIA GeForce RTX 3070 Laptop GPU")
        self.assertIsNone(gpu_auxiliar.choose([nvidia]))
        self.assertIsNone(gpu_auxiliar.choose(gpu_auxiliar.parse_devices("[0 llvmpipe]  queueC=0[1]\n")))
        self.assertIsNone(gpu_auxiliar.choose([nvidia, gpu_auxiliar.Device(1, "NVIDIA GeForce RTX 4090")]))

    def test_without_the_program_there_is_no_auxiliary(self):
        gpu_auxiliar._detect.cache_clear()
        try:
            with patch.dict(os.environ, {"RIFE_NCNN_DIR": tempfile.mkdtemp()}), \
                    patch.object(gpu_auxiliar.subprocess, "run") as run:
                self.assertIsNone(gpu_auxiliar.auxiliary())
            run.assert_not_called()
        finally:
            gpu_auxiliar._detect.cache_clear()

    def test_remote_gpu_and_switch_off_skip_detection(self):
        with patch.object(gpu_auxiliar, "_detect", return_value=AUX) as detect:
            with patch.dict(os.environ, {"INFLUENCER_GPU": "remota"}):
                self.assertIsNone(gpu_auxiliar.auxiliary())
            with patch.dict(os.environ, {"INFLUENCER_GPU": "local", "INFLUENCER_RIFE_AUX": "0"}):
                self.assertIsNone(gpu_auxiliar.auxiliary())
            with patch.dict(os.environ, {"INFLUENCER_GPU": "local", "INFLUENCER_RIFE_AUX": ""}):
                self.assertIs(gpu_auxiliar.auxiliary(), AUX)
        detect.assert_called_once()


class WorkflowTests(unittest.TestCase):
    def test_without_interpolation_the_hd_output_is_only_upscaled(self):
        normal = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x")
        self.assertEqual(normal["20"]["inputs"]["images"], ["19", 0])
        aux = motion.wan_i2v_workflow(start_image="a.png", prompt="p", frames=81, seed=1, prefix="x", interpolate=False)
        self.assertNotIn("19", aux)
        self.assertEqual(aux["20"]["inputs"]["images"], ["18", 0])


class AnimateSceneTests(unittest.TestCase):
    def _animate(self, aux):
        work = Path(tempfile.mkdtemp()) / "scene-01"
        keyframe = work.parent / "keyframe.png"
        keyframe.write_bytes(b"png")
        workflows, calls = [], []

        def run(base, workflow, timeout):
            workflows.append(workflow)
            prefix = workflow["20"]["inputs"]["filename_prefix"][:-3]
            return ([{"filename": f"{prefix}-raw_{i}.png"} for i in range(3)]
                    + [{"filename": f"{prefix}-hd_{i}.png"} for i in range(3)])

        def interpolate(aux_, source, destination, names, skip_first):
            calls.append((sorted(path.name for path in source.glob("*.png")), list(names), skip_first))
            for name in names:
                (destination / name).write_bytes(b"hd")
            return aux_.device.name

        scene = {"motions": ["anda", "para"]}
        with patch.object(motion, "_comfyui_url", return_value="http://x"), \
                patch.object(motion, "_upload_comfy_image", return_value="up.png"), \
                patch.object(motion, "_run_workflow", side_effect=run), \
                patch.object(motion, "_download", return_value=b"png"), \
                patch.object(motion, "discard_outputs"), \
                patch.object(motion, "clip_plan", return_value=[("anda", 81), ("para", 81)]), \
                patch.object(motion.gpu_auxiliar, "auxiliary", return_value=aux), \
                patch.object(motion.gpu_auxiliar, "interpolate", side_effect=interpolate):
            frames = motion.animate_scene(scene, keyframe, 10.0, work, seed=1)
        return work, frames, workflows, calls

    def test_second_gpu_interpolates_each_clip_and_records_it(self):
        work, frames, workflows, calls = self._animate(AUX)
        self.assertTrue(all("19" not in workflow for workflow in workflows))
        # 3 quadros ampliados -> 5 interpolados; o clipe encadeado perde o primeiro (repete o anterior).
        self.assertEqual([(len(names), skip) for _, names, skip in calls], [(5, False), (4, True)])
        self.assertEqual(calls[0][0], ["up-0001.png", "up-0002.png", "up-0003.png"])
        self.assertEqual(len(frames), 9)
        self.assertTrue(all(frame.is_file() for frame in frames))
        done = json.loads((work / "clip-2" / "done.json").read_text(encoding="utf-8"))
        self.assertEqual(done["interpolation"]["gpu"], "AMD Radeon 780M Graphics")

    def test_single_gpu_keeps_rife_in_comfyui(self):
        work, frames, workflows, calls = self._animate(None)
        self.assertTrue(all("19" in workflow for workflow in workflows))
        self.assertEqual(calls, [])
        self.assertEqual(len(frames), 3 + 2)
        self.assertNotIn("interpolation", json.loads((work / "clip-1" / "done.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
