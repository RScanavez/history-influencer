from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .illustration import POSES

LANDMARKS = {
    "nose": 0,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

REQUIRED_LANDMARK_GROUPS = (
    ("left_shoulder", "right_shoulder"),
    ("left_hip", "right_hip"),
    ("left_knee", "right_knee"),
    ("left_ankle", "right_ankle"),
)
HEAD_LANDMARK = "nose"
MIN_POSE_DETECTION_CONFIDENCE = 0.35


@dataclass(frozen=True)
class PoseAssessment:
    expected_pose: str
    detected_people: int
    confidence: float
    passed: bool
    reason: str
    measurements: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _value(landmark: Any, name: str, default: float = 0.0) -> float:
    value = getattr(landmark, name, default)
    return float(value) if value is not None else default


def _xy(landmarks: Sequence[Any], name: str) -> tuple[float, float]:
    landmark = landmarks[LANDMARKS[name]]
    return _value(landmark, "x"), _value(landmark, "y")


def _visibility(landmarks: Sequence[Any], name: str) -> float:
    landmark = landmarks[LANDMARKS[name]]
    return min(_value(landmark, "visibility"), _value(landmark, "presence", 1.0))


def _midpoint(
    landmarks: Sequence[Any],
    left_name: str,
    right_name: str,
) -> tuple[float, float]:
    left = _xy(landmarks, left_name)
    right = _xy(landmarks, right_name)
    return (left[0] + right[0]) / 2, (left[1] + right[1]) / 2


def _angle_at(
    first: tuple[float, float],
    vertex: tuple[float, float],
    last: tuple[float, float],
) -> float:
    first_vector = (first[0] - vertex[0], first[1] - vertex[1])
    last_vector = (last[0] - vertex[0], last[1] - vertex[1])
    first_length = math.hypot(*first_vector)
    last_length = math.hypot(*last_vector)
    if first_length == 0 or last_length == 0:
        return 0.0
    cosine = sum(a * b for a, b in zip(first_vector, last_vector)) / (first_length * last_length)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _body_measurements(landmarks: Sequence[Any]) -> dict[str, float]:
    shoulder = _midpoint(landmarks, "left_shoulder", "right_shoulder")
    hip = _midpoint(landmarks, "left_hip", "right_hip")
    knee = _midpoint(landmarks, "left_knee", "right_knee")
    ankle = _midpoint(landmarks, "left_ankle", "right_ankle")
    torso_height = abs(hip[1] - shoulder[1])
    body_width = abs(ankle[0] - shoulder[0])
    body_line_height = abs(ankle[1] - shoulder[1])
    torso_lean = abs(shoulder[0] - hip[0]) / max(torso_height, 0.01)
    knee_angles = []
    elbow_angles = []
    for side in ("left", "right"):
        try:
            if min(
                _visibility(landmarks, f"{side}_hip"),
                _visibility(landmarks, f"{side}_knee"),
                _visibility(landmarks, f"{side}_ankle"),
            ) >= 0.2:
                knee_angles.append(
                    _angle_at(
                        _xy(landmarks, f"{side}_hip"),
                        _xy(landmarks, f"{side}_knee"),
                        _xy(landmarks, f"{side}_ankle"),
                    )
                )
            if min(
                _visibility(landmarks, f"{side}_shoulder"),
                _visibility(landmarks, f"{side}_elbow"),
                _visibility(landmarks, f"{side}_wrist"),
            ) >= 0.2:
                elbow_angles.append(
                    _angle_at(
                        _xy(landmarks, f"{side}_shoulder"),
                        _xy(landmarks, f"{side}_elbow"),
                        _xy(landmarks, f"{side}_wrist"),
                    )
                )
        except (IndexError, KeyError):
            continue
    segment_slopes = []
    for first, second in ((shoulder, hip), (hip, knee), (knee, ankle)):
        segment_slopes.append(abs(second[1] - first[1]) / max(abs(second[0] - first[0]), 0.03))
    return {
        "shoulder_x": shoulder[0],
        "shoulder_y": shoulder[1],
        "hip_x": hip[0],
        "hip_y": hip[1],
        "knee_x": knee[0],
        "knee_y": knee[1],
        "ankle_x": ankle[0],
        "ankle_y": ankle[1],
        "torso_lean_ratio": torso_lean,
        "body_width": body_width,
        "body_line_height": body_line_height,
        "max_body_segment_slope": max(segment_slopes, default=math.inf),
        "left_right_knee_height_difference": abs(
            _xy(landmarks, "left_knee")[1] - _xy(landmarks, "right_knee")[1]
        ),
        "left_right_ankle_height_difference": abs(
            _xy(landmarks, "left_ankle")[1] - _xy(landmarks, "right_ankle")[1]
        ),
        "most_flexed_knee_angle_degrees": min(knee_angles, default=180.0),
        "most_flexed_elbow_angle_degrees": min(elbow_angles, default=180.0),
        "highest_wrist_y": min(
            _xy(landmarks, "left_wrist")[1],
            _xy(landmarks, "right_wrist")[1],
        ),
        "lowest_shoulder_y": min(
            _xy(landmarks, "left_shoulder")[1],
            _xy(landmarks, "right_shoulder")[1],
        ),
    }


def _pose_mismatch(pose_name: str, measurements: dict[str, float]) -> str | None:
    if pose_name == "standing":
        if not (
            measurements["shoulder_y"] < measurements["hip_y"]
            < measurements["knee_y"] < measurements["ankle_y"]
        ):
            return "a pessoa não está em uma posição vertical claramente detectável"
    elif pose_name == "squat":
        if (
            measurements["most_flexed_knee_angle_degrees"] >= 155
            or measurements["hip_y"] <= measurements["shoulder_y"] + 0.06
        ):
            return "não foi detectada flexão suficiente dos joelhos para um agachamento"
    elif pose_name in {"plank", "push_up"}:
        if (
            measurements["body_width"] < 0.22
            or measurements["body_line_height"] / max(measurements["body_width"], 0.01) > 0.45
            or measurements["max_body_segment_slope"] > 0.8
        ):
            return "a linha do tronco até os tornozelos não está aproximadamente horizontal"
        if pose_name == "plank" and measurements["most_flexed_elbow_angle_degrees"] > 155:
            return "não foi detectado o apoio de antebraços esperado na prancha"
    elif pose_name in {"march", "walk", "balance", "hip_mobility"}:
        asymmetry = max(
            measurements["left_right_knee_height_difference"],
            measurements["left_right_ankle_height_difference"],
        )
        if asymmetry < (0.07 if pose_name == "balance" else 0.045):
            return "não foi detectada a diferença de altura entre as pernas necessária para esta ação"
    elif pose_name == "side_stretch":
        if measurements["torso_lean_ratio"] < 0.12:
            return "o tronco não parece inclinado lateralmente"
    elif pose_name == "hamstring_stretch":
        if (
            measurements["torso_lean_ratio"] < 0.15
            and measurements["most_flexed_knee_angle_degrees"] < 155
        ):
            return "não foi detectada a inclinação ou extensão da perna esperada no alongamento"
    elif pose_name == "seated_posture":
        if (
            abs(measurements["hip_y"] - measurements["knee_y"]) > 0.18
            or measurements["ankle_y"] <= measurements["knee_y"]
        ):
            return "não foi detectada uma posição sentada com joelhos flexionados"
    elif pose_name == "dumbbell_press":
        if measurements["highest_wrist_y"] >= measurements["lowest_shoulder_y"] - 0.05:
            return "as mãos não estão elevadas acima dos ombros como no press"
    return None


def assess_pose_landmarks(
    pose_name: str,
    detected_poses: Sequence[Sequence[Any]],
    *,
    minimum_visibility: float = 0.35,
) -> PoseAssessment:
    if pose_name not in POSES:
        valid_poses = ", ".join(sorted(POSES))
        raise ValueError(f"pose deve ser um dos seguintes valores: {valid_poses}")
    if not detected_poses:
        return PoseAssessment(pose_name, 0, 0.0, False, "nenhuma pessoa detectada", {})
    if len(detected_poses) != 1:
        return PoseAssessment(
            pose_name,
            len(detected_poses),
            0.0,
            False,
            "a imagem precisa conter exatamente uma pessoa detectável",
            {},
        )

    landmarks = detected_poses[0]
    try:
        if len(landmarks) <= max(LANDMARKS.values()):
            return PoseAssessment(
                pose_name,
                1,
                0.0,
                False,
                "o detector retornou pontos corporais incompletos",
                {},
            )
        group_confidence = [
            max(_visibility(landmarks, name) for name in group)
            for group in REQUIRED_LANDMARK_GROUPS
        ]
        confidence = sum(group_confidence) / len(group_confidence)
        if any(value < minimum_visibility for value in group_confidence):
            return PoseAssessment(
                pose_name,
                1,
                round(confidence, 3),
                False,
                "pontos corporais essenciais estão encobertos ou pouco confiáveis",
                {},
            )
        if _visibility(landmarks, HEAD_LANDMARK) < minimum_visibility:
            return PoseAssessment(
                pose_name,
                1,
                round(confidence, 3),
                False,
                "a cabeça não foi detectada com confiança (figura sem cabeça ou rosto encoberto)",
                {},
            )
        measurements = _body_measurements(landmarks)
    except (IndexError, AttributeError, TypeError, ValueError) as error:
        return PoseAssessment(
            pose_name,
            1,
            0.0,
            False,
            f"não foi possível interpretar os pontos corporais: {error}",
            {},
        )
    mismatch = _pose_mismatch(pose_name, measurements)
    return PoseAssessment(
        pose_name,
        1,
        round(confidence, 3),
        mismatch is None,
        mismatch or "pose compatível com a classe solicitada",
        {name: round(value, 4) for name, value in measurements.items()},
    )


def validate_pose_image(
    image_path: Path,
    pose_name: str,
    *,
    model_path: Path | None = None,
) -> PoseAssessment:
    asset_path = model_path or Path(os.environ.get("MEDIAPIPE_POSE_MODEL_PATH", ""))
    if not str(asset_path) or not asset_path.is_file():
        raise RuntimeError(
            "Configure MEDIAPIPE_POSE_MODEL_PATH com o arquivo pose_landmarker_lite.task."
        )
    if not image_path.is_file():
        raise FileNotFoundError(f"A imagem a validar não existe: {image_path}")
    try:
        import mediapipe as mp
    except ImportError as error:
        raise RuntimeError("Instale as dependências do projeto para validar as poses com MediaPipe.") from error

    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(asset_path)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=2,
        min_pose_detection_confidence=MIN_POSE_DETECTION_CONFIDENCE,
        min_pose_presence_confidence=0.35,
    )
    try:
        with mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
            image = mp.Image.create_from_file(str(image_path))
            result = landmarker.detect(image)
    except Exception as error:
        raise RuntimeError(f"Falha na validação MediaPipe da imagem {image_path.name}: {error}") from error
    return assess_pose_landmarks(pose_name, result.pose_landmarks)
