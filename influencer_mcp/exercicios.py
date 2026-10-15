"""Biblioteca de exercícios para as cenas que demonstram um exercício (pedido do usuário, 2026-10-04).

Os vídeos de exercício não correspondiam ao pedido: o SDXL segue mal posições do corpo descritas só em texto e o
Wan, acelerado pelo lightx2v, mexe pouco e obedece pouco ao prompt. Cada cena com `"exercise": "<id>"` passa a ter:

1. três etapas de amostragem no Wan (a primeira sem o acelerador e com CFG 3,5), em motion.wan_i2v_workflow;
2. frase de movimento escrita pelo sistema, uma ação por clipe, com direção, ritmo e o que fica parado;
3. duas imagens-chave, posição inicial e final, com o esqueleto OpenPose da biblioteca (a final é refeita a partir
   da inicial, para manter cenário e roupa). Desde 2026-10-06 cada posição é uma especificação biomecânica (ângulo
   de cada articulação, em biomecanica.py): o esqueleto 3D com as proporções do corpo, o texto membro a membro da
   imagem e do vídeo e o vídeo de esqueleto saem todos dela;
4. animação conduzida por um vídeo de esqueleto (Wan 2.2 Fun Control) da posição inicial à final e de volta,
   quando o modelo existe na GPU usada; senão, quadro inicial e final (Wan FLF2V): subida e descida ancoradas;
5. verificação automática com MediaPipe: ângulos de joelho, quadril, ombro e cotovelo das imagens-chave e dos
   quadros comparados com o esperado; o que não bater é refeito (até 3 tentativas) e o resultado fica registrado.

Coordenadas no quadro de referência 576x1024 (o mesmo de illustration.POSES). Nos 18 pontos OpenPose, "left"/"right" são os
lados da imagem: de frente, o "left" é o lado direito da pessoa.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw

from . import biomecanica as bio
from .biomecanica import Arm, Leg, Pose
from .illustration import OPENPOSE_COLORS, OPENPOSE_LIMBS
from .winproc import quiet

REF_WIDTH, REF_HEIGHT = 576, 1024
Point = tuple[float, float] | None


@dataclass(frozen=True)
class Exercise:
    id: str
    label: str  # nome em português, para o roteirista e a folha de revisão
    yaw: float  # câmera: 0 = de frente; ~35-60 = três-quartos (mostra o que vai para a frente); 80+ = perfil
    start_pose: Pose
    end_pose: Pose

    @property
    def view(self) -> str:
        return bio.view_of(self.yaw)

    def pose(self, which: str) -> Pose:
        return self.start_pose if which == "start" else self.end_pose

    @cached_property
    def _offset(self) -> float:
        # Mesmo deslocamento horizontal nas duas posições: o corpo não "pula" de uma para a outra.
        return bio.centered_offset(bio.project(bio.skeleton(self.start_pose), self.yaw))

    def points_at(self, t: float) -> list[Point]:
        """18 pontos OpenPose (pixels do quadro 576x1024) na fração t da repetição (0 = início, 1 = fim)."""
        return bio.project(bio.skeleton(bio.interpolate_pose(self.start_pose, self.end_pose, t)), self.yaw, self._offset)

    def points(self, which: str) -> list[Point]:
        return self.points_at(0.0 if which == "start" else 1.0)

    @property
    def start_body(self) -> str:
        return bio.describe(self.start_pose, self.yaw)

    @property
    def end_body(self) -> str:
        return bio.describe(self.end_pose, self.yaw)

    @property
    def up(self) -> str:
        return bio.movement(self.start_pose, self.end_pose)

    @property
    def down(self) -> str:
        return bio.movement(self.end_pose, self.start_pose)

    @property
    def still(self) -> str:
        return bio.still_parts(self.start_pose, self.end_pose)

    def avoid(self, which: str) -> str:
        """Negativo da imagem-chave: o que o SDXL fazia errado nos testes de 2026-10-06 (sentar, segunda pessoa,
        objeto na mão em exercício de mãos livres, barriga de fora e músculos marcados)."""
        base = ("(second person:1.4), (small figure in the background:1.4), (drawing of a person on the wall:1.3), "
                "(abs:1.5), (six-pack:1.5), (crop top:1.5), (sports bra:1.5), (bare midriff:1.5), "
                "(muscular arms:1.4), (barefoot:1.5)")
        if self.id not in HOLDS_OBJECTS:
            base += ", (holding a ball:1.4), (holding an object:1.3), (ball in hand:1.4)"
        if bio.standing(self.pose(which)):
            return ("(sitting:1.5), (seated:1.5), (sitting on a chair:1.6), (sitting on a stool:1.6), "
                    f"(perched on furniture:1.4), {base}")
        return f"(standing up:1.3), {base}"


# Posições de partida comuns. Lados = da pessoa; o lado direito é o mais perto da câmera nas vistas de três-quartos,
# então é ele que se move e a mão de apoio (esquerda) fica na parede. Apoio na parede, não na cadeira: no primeiro
# teste real (2026-10-06) o SDXL transformava a cadeira de apoio em assento e a personagem saía sentada.
_SUPPORT_WALL = "palm resting flat on a wall beside the body"
_SUPPORT_ARM = Arm(elevation=70, plane=0, elbow=20, bend="up")
_SUPPORTED = Pose(arm_l=_SUPPORT_ARM, contacts={"hand_l": _SUPPORT_WALL})
_SEATED_ARM = Arm(elevation=15, plane=-20, elbow=20, bend="down")
_SEATED = Pose(arm_r=_SEATED_ARM, arm_l=_SEATED_ARM, leg_r=Leg(flex=90, knee=90), leg_l=Leg(flex=90, knee=90),
               trunk_flex=5, seated=True, contacts={"hand_r": "holding the side of the chair seat",
                                                    "hand_l": "holding the side of the chair seat"})
_ARMS_DOWN = Pose(arm_r=Arm(5, 0, 5), arm_l=Arm(5, 0, 5))
_WALL = {"hand_r": "palm flat on the wall at shoulder height", "hand_l": "palm flat on the wall at shoulder height"}
_WALL_START = Pose(arm_r=Arm(108, 90, 5), arm_l=Arm(108, 90, 5), lean=18, contacts=_WALL)


def _wall_end() -> Pose:
    """Mais perto da parede com as mãos no mesmo lugar (o cotovelo dobra o que for preciso)."""
    end = Pose(lean=27, contacts=_WALL)
    hands = bio.skeleton(_WALL_START)
    return replace(end, arm_r=bio.reach(end, "r", hands["wrist_r"], 90, "up"),
                   arm_l=bio.reach(end, "l", hands["wrist_l"], 90, "up"))


EXERCISES: dict[str, Exercise] = {item.id: item for item in (
    Exercise("sit_to_stand", "Sentar e levantar da cadeira", 60, _SEATED, Pose()),
    Exercise("seated_knee_extension", "Extensão de joelho sentada", 60, _SEATED,
             replace(_SEATED, leg_r=Leg(flex=90, knee=10), support=("l",))),
    Exercise("seated_march", "Marcha sentada", 60, _SEATED,
             replace(_SEATED, leg_r=Leg(flex=115, knee=95), support=("l",))),
    Exercise("seated_calf_raise", "Elevação de calcanhar sentada", 60, _SEATED,
             replace(_SEATED, leg_r=Leg(flex=90, knee=90, heel=40), leg_l=Leg(flex=90, knee=90, heel=40))),
    Exercise("standing_calf_raise", "Elevação de calcanhar em pé, com apoio", 35, _SUPPORTED,
             replace(_SUPPORTED, leg_r=Leg(heel=40), leg_l=Leg(heel=40))),
    Exercise("wall_push_up", "Flexão na parede", 80, _WALL_START, _wall_end()),
    Exercise("biceps_curl", "Rosca de bíceps", 35, _ARMS_DOWN,
             Pose(arm_r=Arm(12, 90, 135), arm_l=Arm(12, 90, 135))),
    Exercise("lateral_raise", "Elevação lateral dos braços", 0, Pose(arm_r=Arm(8, 0, 5), arm_l=Arm(8, 0, 5)),
             Pose(arm_r=Arm(88, 0, 8), arm_l=Arm(88, 0, 8))),
    Exercise("overhead_press", "Desenvolvimento acima da cabeça", 0,
             Pose(arm_r=Arm(85, 0, 95, "up"), arm_l=Arm(85, 0, 95, "up")),
             Pose(arm_r=Arm(168, 5, 8, "up"), arm_l=Arm(168, 5, 8, "up"))),
    Exercise("standing_march", "Marcha no lugar", 55, Pose(),
             Pose(leg_r=Leg(flex=80, knee=90), arm_l=Arm(35, 90, 40), arm_r=Arm(25, -90, 15), support=("l",))),
    Exercise("side_leg_raise", "Abertura lateral da perna, com apoio", 0, _SUPPORTED,
             replace(_SUPPORTED, leg_r=Leg(abduction=30), support=("l",))),
    # Mãos entrelaçadas à frente: começam diante do quadril e sobem até a altura dos ombros enquanto o quadril vai
    # para trás.
    Exercise("mini_squat", "Miniagachamento", 40, Pose(arm_r=Arm(18, 120, 10), arm_l=Arm(18, 120, 10),
                                                       contacts={"hands": "empty hands clasped together in front of the hips"}),
             Pose(leg_r=Leg(flex=60, knee=75), leg_l=Leg(flex=60, knee=75), trunk_flex=30,
                  arm_r=Arm(105, 103, 10), arm_l=Arm(105, 103, 10),
                  contacts={"hands": "empty hands clasped together in front of the chest, arms extended forward"})),
    Exercise("single_leg_balance", "Equilíbrio numa perna, com apoio", 50, _SUPPORTED,
             replace(_SUPPORTED, leg_r=Leg(flex=55, knee=75), support=("l",))),
    Exercise("side_stretch", "Alongamento lateral", 0, Pose(), Pose(arm_r=Arm(165, 0, 15, "in"), trunk_side=18)),
)}


def library_enabled() -> bool:
    """Linha "- biblioteca-exercicios: sim|não" de fluxo_aprovacao.md. Desligada (2026-10-08), vídeos novos não usam
    a biblioteca: nada de "exercise" nas cenas, e a fala volta a ser livre. Vídeos já feitos continuam como estão.
    Sem a linha, vale ligada (comportamento antigo)."""
    from .flags import flag_enabled

    return flag_enabled("biblioteca-exercicios")


def get(exercise_id: str) -> Exercise:
    if exercise_id not in EXERCISES:
        raise ValueError(f"Exercício desconhecido: {exercise_id!r}. Use um de: {', '.join(EXERCISES)}.")
    return EXERCISES[exercise_id]


def catalog() -> list[dict[str, str]]:
    return [{"id": item.id, "label": item.label} for item in EXERCISES.values()]


def _pronouns(gender: str) -> tuple[str, str]:
    return ("he", "his") if gender.lower().startswith(("m", "masc")) else ("she", "her")


# Exercícios em que a mão segura algo (um objeto leve em cada mão: garrafa de água ou halter). Nos demais, mãos
# livres: pedir uma bola na mão de um exercício de mãos livres fazia o SDXL inventar a posição (teste de 2026-10-06).
HOLDS_OBJECTS = {"biceps_curl", "lateral_raise", "overhead_press"}


def keyframe_prompt(exercise: Exercise, which: str, scene_prompt: str) -> str:
    """Posição do corpo membro a membro e, com peso, cenário, roupa e objetos do roteiro (sem o peso, a roupa e os
    objetos se perdiam atrás da descrição do corpo)."""
    body = exercise.start_body if which == "start" else exercise.end_body
    return f"{body}; full body visible head to feet, one single person, ({scene_prompt.strip().rstrip('.')}:1.25)"


# Pernas paradas também são conferidas (primeiro teste: a pessoa saiu sentada num exercício em pé e passou, porque
# só os braços se moviam). Tolerância larga: só pega em pé x sentada, não pequenas diferenças.
STATIC_TOLERANCE = 45


def motion_prompt(exercise: Exercise, direction: str, gender: str) -> str:
    """Uma ação por clipe: o que cada articulação faz (de quanto a quanto), ritmo e o que fica parado; câmera fixa."""
    subject, _ = _pronouns(gender)
    if direction == "up":
        action = exercise.up
    elif direction == "down":
        action = exercise.down
    else:  # repetição completa (vídeo de esqueleto)
        action = f"{exercise.up}, holds for a moment, then {exercise.down}"
    still = f"; {exercise.still}" if exercise.still else ""
    return (f"{subject.capitalize()} slowly {action}, smooth and controlled, about two seconds each way{still}. "
            "Limbs keep their length, joints bend naturally. Static camera, same framing, no cuts")


# ---------------------------------------------------------------- esqueleto OpenPose (18 pontos)

def pose_points(exercise: Exercise, which: str) -> list[Point]:
    """18 pontos OpenPose em pixels do quadro de referência (576x1024)."""
    return exercise.points(which)


def normalized(points: Sequence[Point], width: float = REF_WIDTH, height: float = REF_HEIGHT) -> list[Point]:
    return [None if p is None else (p[0] / width, p[1] / height) for p in points]


def draw_openpose(points: Sequence[Point], width: int, height: int, *, line_width: int | None = None) -> Image.Image:
    """Desenha pontos normalizados (0-1) no padrão de cores do OpenPose, sobre fundo preto."""
    line_width = line_width or max(3, round(min(width, height * 0.5625) / 60))
    radius = line_width
    scaled = [None if p is None else (p[0] * width, p[1] * height) for p in points]
    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index, (start, end) in enumerate(OPENPOSE_LIMBS):
        if scaled[start] is not None and scaled[end] is not None:
            draw.line((scaled[start], scaled[end]), fill=OPENPOSE_COLORS[index], width=line_width)
    for index, point in enumerate(scaled):
        if point is None:
            continue
        r = radius if index < 14 else max(3, round(radius * 0.625))
        draw.ellipse((point[0] - r, point[1] - r, point[0] + r, point[1] + r), fill=OPENPOSE_COLORS[index % 18])
    return image


def render_guide(exercise: Exercise, which: str, destination: Path) -> Path:
    """Guia OpenPose vertical (512x896) da posição inicial ou final, para a imagem-chave."""
    draw_openpose(normalized(exercise.points(which)), 512, 896, line_width=8).save(destination)
    return destination


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def interpolate(start: Sequence[Point], end: Sequence[Point], t: float) -> list[Point]:
    out: list[Point] = []
    for a, b in zip(start, end):
        if a is None or b is None:
            out.append(a or b)
        else:
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def rep_curve(frames: int) -> list[float]:
    """Repetição completa: da posição inicial à final e de volta, com pausa curta no topo (0..1..0)."""
    values = []
    for index in range(frames):
        phase = index / max(frames - 1, 1)
        values.append(_ease(min(1.0, phase / 0.45)) if phase <= 0.5 else _ease(min(1.0, (1 - phase) / 0.45)))
    return values


def _placed(points: Sequence[Point], width: int, height: int) -> list[Point]:
    """Pontos do quadro de referência (576x1024) no quadro do vídeo, centralizados como o guia (widen_pose_guide)."""
    scale = min(width / REF_WIDTH, height / REF_HEIGHT)
    ox, oy = (width - REF_WIDTH * scale) / 2, (height - REF_HEIGHT * scale) / 2
    return [None if p is None else (ox + p[0] * scale, oy + p[1] * scale) for p in points]


def fit_to(model: Sequence[Point], detected: Sequence[Point]) -> tuple[float, float, float]:
    """Escala e deslocamento que levam o esqueleto da biblioteca para cima da pessoa da imagem-chave."""
    pairs = [(a, b) for a, b in zip(model[:14], detected[:14]) if a is not None and b is not None]
    if len(pairs) < 4:
        return 1.0, 0.0, 0.0
    ax = sum(a[0] for a, _ in pairs) / len(pairs)
    ay = sum(a[1] for a, _ in pairs) / len(pairs)
    bx = sum(b[0] for _, b in pairs) / len(pairs)
    by = sum(b[1] for _, b in pairs) / len(pairs)
    spread_a = sum((a[0] - ax) ** 2 + (a[1] - ay) ** 2 for a, _ in pairs)
    spread_b = sum((b[0] - bx) ** 2 + (b[1] - by) ** 2 for _, b in pairs)
    scale = math.sqrt(spread_b / spread_a) if spread_a else 1.0
    return scale, bx - scale * ax, by - scale * ay


SPEC_VERSION = "biomecanica-1"


def legacy_control_video(start: Sequence[Point] | None, end: Sequence[Point] | None, frames: int,
                         size: tuple[int, int], destination: Path, ffmpeg: str, fps: int = 16) -> Path:
    """Vídeos com imagens-chave anteriores a 2026-10-06: interpola os pontos detectados nas duas imagens."""
    if not start or not end:
        raise RuntimeError("Pessoa não detectada nas imagens-chave antigas; refaça as imagens desta cena.")
    width, height = size
    with tempfile.TemporaryDirectory() as folder:
        for index, t in enumerate(rep_curve(frames)):
            draw_openpose(interpolate(start, end, t), width, height).save(Path(folder) / f"f-{index:04d}.png")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(Path(folder) / "f-%04d.png"),
                        "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(destination)],
                       check=True, capture_output=True, timeout=300, **quiet())
    return destination


def control_video(exercise: Exercise, frames: int, size: tuple[int, int], destination: Path, ffmpeg: str,
                  detected_start: Sequence[Point] | None = None, fps: int = 16) -> Path:
    """Vídeo de esqueleto da repetição para o Fun Control: a pose é interpolada nos ÂNGULOS (os membros mantêm o
    comprimento) e, se a pessoa foi detectada na imagem-chave inicial, o esqueleto é encaixado em cima dela."""
    width, height = size
    scale, dx, dy = 1.0, 0.0, 0.0
    if detected_start:
        target = [None if p is None else (p[0] * width, p[1] * height) for p in detected_start]
        scale, dx, dy = fit_to(_placed(exercise.points("start"), width, height), target)
    with tempfile.TemporaryDirectory() as folder:
        for index, t in enumerate(rep_curve(frames)):
            placed = _placed(exercise.points_at(t), width, height)
            frame = [None if p is None else ((p[0] * scale + dx) / width, (p[1] * scale + dy) / height) for p in placed]
            draw_openpose(frame, width, height).save(Path(folder) / f"f-{index:04d}.png")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(Path(folder) / "f-%04d.png"),
                        "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(destination)],
                       check=True, capture_output=True, timeout=300, **quiet())
    return destination


# ---------------------------------------------------------------- verificação (técnica 5)

# Articulações e os três pontos (OpenPose) que formam cada ângulo, dos dois lados.
JOINTS = {
    "knee": ((8, 9, 10), (11, 12, 13)),
    "elbow": ((2, 3, 4), (5, 6, 7)),
    "shoulder": ((8, 2, 3), (11, 5, 6)),
    "hip": ((2, 8, 9), (5, 11, 12)),
}
MOVING_DEGREES = 20  # articulação que muda menos que isso entre as poses não entra na verificação


def _angle(a: Point, b: Point, c: Point) -> float | None:
    if a is None or b is None or c is None:
        return None
    v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if not n1 or not n2:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))


def angles(points: Sequence[Point]) -> dict[str, list[float | None]]:
    """Ângulos por articulação, ordenados (menor, maior): não importa qual lado é qual (espelhamento)."""
    result = {}
    for joint, sides in JOINTS.items():
        values = [_angle(*(points[i] for i in side)) for side in sides]
        known = sorted(v for v in values if v is not None)
        result[joint] = known + [None] * (2 - len(known))
    return result


def _pixels(points: Sequence[Point], width: float, height: float) -> list[Point]:
    return [None if p is None else (p[0] * width, p[1] * height) for p in points]


def expected_angles(exercise: Exercise) -> tuple[dict, dict]:
    return angles(exercise.points("start")), angles(exercise.points("end"))


def moving_joints(start: dict, end: dict) -> list[tuple[str, int]]:
    return [(joint, i) for joint in JOINTS for i in range(2)
            if start[joint][i] is not None and end[joint][i] is not None
            and abs(end[joint][i] - start[joint][i]) >= MOVING_DEGREES]


def check_pose(measured: dict | None, exercise: Exercise, which: str) -> dict[str, Any]:
    """A imagem está mais perto da posição pedida que da outra, em cada articulação que se move?"""
    if measured is None:
        return {"ok": False, "score": -1.0, "reason": "nenhuma pessoa (ou mais de uma) detectada com confiança"}
    start, end = expected_angles(exercise)
    target, other = (start, end) if which == "start" else (end, start)
    joints = moving_joints(start, end)
    # Articulação encoberta (de perfil, a perna ou o braço de trás) não conta como erro: o teste de 2026-10-04
    # marcava imagens certas de "sentar e levantar" e "flexão na parede" como erradas por isso.
    measurable = [(joint, i) for joint, i in joints if measured[joint][i] is not None]
    if not measurable:
        return {"ok": None, "score": 0.5, "reason": "não deu para medir (articulações encobertas)"}
    wrong = [f"{joint} {round(measured[joint][i])}° (esperado ~{round(target[joint][i])}°)" for joint, i in measurable
             if abs(measured[joint][i] - target[joint][i]) >= abs(measured[joint][i] - other[joint][i])]
    static = [(joint, i) for joint in ("knee", "hip") for i in range(2) if (joint, i) not in joints
              and measured[joint][i] is not None and target[joint][i] is not None]
    wrong += [f"{joint} parado {round(measured[joint][i])}° (esperado ~{round(target[joint][i])}°)" for joint, i in static
              if abs(measured[joint][i] - target[joint][i]) > STATIC_TOLERANCE]
    measurable += static
    score = 1 - len(wrong) / len(measurable)
    return {"ok": not wrong, "score": round(score, 2),
            "reason": "posição compatível" if not wrong else "fora da posição: " + ", ".join(wrong)}


def rep_progress(frames: Sequence[dict | None], start: dict, end: dict) -> dict[str, Any]:
    """Quanto da repetição os quadros fizeram (0 = ficou parado na posição inicial, 1 = chegou à final)."""
    joints = moving_joints(start, end)
    if not joints:
        return {"ok": None, "progress": None, "reason": "movimento pequeno demais para medir ângulos"}
    progress = []
    for measured in frames:
        if measured is None:
            continue
        values = [(measured[j][i] - start[j][i]) / (end[j][i] - start[j][i]) for j, i in joints if measured[j][i] is not None]
        if values:
            progress.append(sum(values) / len(values))
    if not progress:
        return {"ok": False, "progress": 0.0, "reason": "pessoa não detectada nos quadros"}
    best = max(progress)
    ok = best >= REP_MIN_PROGRESS
    return {"ok": ok, "progress": round(best, 2),
            "reason": "movimento completo" if ok else f"o movimento chegou só a {round(best * 100)}% da posição final"}


REP_MIN_PROGRESS = 0.6

# MediaPipe (33 pontos) → OpenPose (18). OpenPose 2 = ombro direito da pessoa = lado esquerdo da imagem, de frente.
_MP_TO_OPENPOSE = {0: 0, 2: 12, 3: 14, 4: 16, 5: 11, 6: 13, 7: 15, 8: 24, 9: 26, 10: 28, 11: 23, 12: 25, 13: 27,
                   14: 5, 15: 2, 16: 8, 17: 7}


class Detector:
    """MediaPipe Pose (modelo em MEDIAPIPE_POSE_MODEL_PATH), reaproveitado entre imagens."""

    def __init__(self) -> None:
        self.landmarker = None
        path = Path(os.environ.get("MEDIAPIPE_POSE_MODEL_PATH", "") or "-")
        try:
            import mediapipe as mp

            if path.is_file():
                options = mp.tasks.vision.PoseLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE, num_poses=2,
                    min_pose_detection_confidence=0.35, min_pose_presence_confidence=0.35)
                self.landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
                self._mp = mp
        except Exception:  # noqa: BLE001 - sem MediaPipe a verificação é pulada e isso fica registrado
            self.landmarker = None

    @property
    def available(self) -> bool:
        return self.landmarker is not None

    def points(self, image_path: Path) -> list[Point] | None:
        """18 pontos normalizados da única pessoa na imagem (None se não houver exatamente uma)."""
        if not self.available:
            return None
        result = self.landmarker.detect(self._mp.Image.create_from_file(str(image_path)))
        if len(result.pose_landmarks) != 1:
            return None
        marks = result.pose_landmarks[0]
        points: list[Point] = [None] * 18
        for op_index, mp_index in _MP_TO_OPENPOSE.items():
            mark = marks[mp_index]
            if min(getattr(mark, "visibility", 1) or 0, getattr(mark, "presence", 1) or 1) >= 0.3:
                points[op_index] = (mark.x, mark.y)
        if points[2] and points[5]:
            points[1] = ((points[2][0] + points[5][0]) / 2, (points[2][1] + points[5][1]) / 2)
        return points

    def angles(self, image_path: Path) -> dict | None:
        points = self.points(image_path)
        if points is None:
            return None
        with Image.open(image_path) as opened:
            width, height = opened.size
        return angles(_pixels(points, width, height))

    def close(self) -> None:
        if self.landmarker is not None:
            self.landmarker.close()
