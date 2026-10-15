import unittest
from types import SimpleNamespace

from influencer_mcp.illustration import POSES
from influencer_mcp.pose_validation import LANDMARKS, assess_pose_landmarks


def _blank_pose() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(x=0.5, y=0.5, visibility=0.0, presence=0.0)
        for _ in range(33)
    ]


def _set_point(
    landmarks: list[SimpleNamespace],
    index: int,
    x: float,
    y: float,
    visibility: float = 0.95,
) -> None:
    landmarks[index] = SimpleNamespace(x=x, y=y, visibility=visibility, presence=visibility)


def _standing_pose() -> list[SimpleNamespace]:
    landmarks = _blank_pose()
    points = {
        0: (0.50, 0.10),
        11: (0.43, 0.20), 12: (0.57, 0.20),
        13: (0.36, 0.35), 14: (0.64, 0.35),
        15: (0.34, 0.50), 16: (0.66, 0.50),
        23: (0.44, 0.48), 24: (0.56, 0.48),
        25: (0.45, 0.69), 26: (0.55, 0.69),
        27: (0.45, 0.90), 28: (0.55, 0.90),
    }
    for index, (x, y) in points.items():
        _set_point(landmarks, index, x, y)
    return landmarks


def _squat_pose() -> list[SimpleNamespace]:
    landmarks = _standing_pose()
    points = {
        23: (0.43, 0.51), 24: (0.57, 0.51),
        25: (0.31, 0.64), 26: (0.69, 0.64),
        27: (0.35, 0.89), 28: (0.65, 0.89),
    }
    for index, (x, y) in points.items():
        _set_point(landmarks, index, x, y)
    return landmarks


def _plank_pose() -> list[SimpleNamespace]:
    landmarks = _blank_pose()
    points = {
        0: (0.22, 0.42),
        11: (0.30, 0.45), 12: (0.32, 0.47),
        13: (0.30, 0.56), 14: (0.32, 0.58),
        15: (0.19, 0.57), 16: (0.21, 0.59),
        23: (0.55, 0.48), 24: (0.57, 0.50),
        25: (0.69, 0.50), 26: (0.71, 0.52),
        27: (0.84, 0.51), 28: (0.86, 0.53),
    }
    for index, (x, y) in points.items():
        _set_point(landmarks, index, x, y)
    return landmarks


class MediaPipePoseAssessmentTests(unittest.TestCase):
    def test_every_openpose_guide_matches_its_validator_pose_class(self) -> None:
        for pose_name, pose in POSES.items():
            with self.subTest(pose=pose_name):
                landmarks = _blank_pose()
                points = {
                    "nose": pose.head,
                    "left_shoulder": pose.shoulder_left,
                    "right_shoulder": pose.shoulder_right,
                    "left_elbow": pose.elbow_left,
                    "right_elbow": pose.elbow_right,
                    "left_wrist": pose.wrist_left,
                    "right_wrist": pose.wrist_right,
                    "left_hip": pose.hip_left,
                    "right_hip": pose.hip_right,
                    "left_knee": pose.knee_left,
                    "right_knee": pose.knee_right,
                    "left_ankle": pose.ankle_left,
                    "right_ankle": pose.ankle_right,
                }
                for name, (x, y) in points.items():
                    _set_point(landmarks, LANDMARKS[name], x / 576, y / 1024)

                result = assess_pose_landmarks(pose_name, [landmarks])

                self.assertTrue(result.passed, result.reason)

    def test_accepts_a_single_person_in_the_requested_pose(self) -> None:
        for pose_name, landmarks in (
            ("standing", _standing_pose()),
            ("squat", _squat_pose()),
            ("plank", _plank_pose()),
        ):
            with self.subTest(pose=pose_name):
                result = assess_pose_landmarks(pose_name, [landmarks])
                self.assertTrue(result.passed, result.reason)
                self.assertEqual(result.detected_people, 1)
                self.assertGreater(result.confidence, 0.9)

    def test_rejects_a_pose_that_does_not_match_the_requested_action(self) -> None:
        result = assess_pose_landmarks("plank", [_standing_pose()])
        self.assertFalse(result.passed)
        self.assertIn("horizontal", result.reason)

    def test_rejects_a_plank_with_a_bent_leg_out_of_alignment(self) -> None:
        landmarks = _plank_pose()
        _set_point(landmarks, 25, 0.72, 0.35)
        _set_point(landmarks, 26, 0.74, 0.37)
        result = assess_pose_landmarks("plank", [landmarks])
        self.assertFalse(result.passed)
        self.assertIn("horizontal", result.reason)

    def test_rejects_missing_or_multiple_people(self) -> None:
        self.assertFalse(assess_pose_landmarks("squat", []).passed)
        result = assess_pose_landmarks("squat", [_squat_pose(), _squat_pose()])
        self.assertFalse(result.passed)
        self.assertIn("exatamente uma pessoa", result.reason)
        self.assertEqual(result.detected_people, 2)

    def test_rejects_low_confidence_required_landmarks(self) -> None:
        landmarks = _standing_pose()
        _set_point(landmarks, 11, 0.43, 0.20, visibility=0.05)
        _set_point(landmarks, 12, 0.57, 0.20, visibility=0.10)
        result = assess_pose_landmarks("standing", [landmarks])
        self.assertFalse(result.passed)
        self.assertIn("pouco confiáveis", result.reason)

    def test_rejects_a_figure_without_a_detectable_head(self) -> None:
        landmarks = _standing_pose()
        _set_point(landmarks, LANDMARKS["nose"], 0.50, 0.10, visibility=0.05)
        result = assess_pose_landmarks("standing", [landmarks])
        self.assertFalse(result.passed)
        self.assertIn("cabeça", result.reason)

    def test_rejects_unknown_pose_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "pose deve ser"):
            assess_pose_landmarks("backflip", [_standing_pose()])


if __name__ == "__main__":
    unittest.main()
