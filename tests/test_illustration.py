import tempfile
import unittest
from pathlib import Path

from PIL import Image

from influencer_mcp.illustration import OPENPOSE_LIMBS, POSES, render_openpose_guide


class OpenPoseGuideTests(unittest.TestCase):
    def test_every_supported_pose_renders_a_distinct_control_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            rendered: dict[str, bytes] = {}
            for pose in sorted(POSES):
                destination = directory / f"{pose}-pose.png"
                render_openpose_guide(pose_name=pose, destination=destination)

                with Image.open(destination) as image:
                    expected_size = (896, 512) if pose in {"plank", "push_up"} else (512, 896)
                    self.assertEqual(image.size, expected_size)
                    if pose in {"plank", "push_up"}:
                        bounds = image.getbbox()
                        self.assertIsNotNone(bounds)
                        assert bounds is not None
                        self.assertGreaterEqual(bounds[0], 90)
                        self.assertLessEqual(bounds[2], image.width - 90)
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.format, "PNG")
                    self.assertGreater(len(image.getcolors(maxcolors=1_000_000) or []), len(OPENPOSE_LIMBS))
                rendered[pose] = destination.read_bytes()

            self.assertNotEqual(rendered["standing"], rendered["squat"])
            self.assertNotEqual(rendered["squat"], rendered["plank"])

    def test_rejects_unsupported_pose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "pose deve ser"):
                render_openpose_guide(
                    pose_name="backflip",
                    destination=Path(temp_dir) / "invalid.png",
                )

if __name__ == "__main__":
    unittest.main()
