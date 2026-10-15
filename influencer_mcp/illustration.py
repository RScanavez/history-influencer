from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

FRAME_WIDTH = 576
FRAME_HEIGHT = 1024
SCALE = 2
INK = (28, 35, 58)
SHIRT = (27, 65, 129)
SHIRT_LIGHT = (51, 111, 190)
PANTS = (48, 55, 79)
PANTS_LIGHT = (74, 84, 111)
SKIN = (199, 126, 91)
SKIN_LIGHT = (232, 164, 122)
SOLE = (255, 255, 255)


@dataclass(frozen=True)
class FigurePose:
    head: tuple[int, int]
    neck: tuple[int, int]
    shoulder_left: tuple[int, int]
    shoulder_right: tuple[int, int]
    elbow_left: tuple[int, int]
    elbow_right: tuple[int, int]
    wrist_left: tuple[int, int]
    wrist_right: tuple[int, int]
    hip_left: tuple[int, int]
    hip_right: tuple[int, int]
    knee_left: tuple[int, int]
    knee_right: tuple[int, int]
    ankle_left: tuple[int, int]
    ankle_right: tuple[int, int]
    face_left: bool = False


POSES: dict[str, FigurePose] = {
    "standing": FigurePose(
        (288, 226), (288, 301), (244, 331), (332, 331), (219, 421), (359, 421),
        (210, 508), (368, 508), (263, 524), (313, 524), (264, 684), (312, 684),
        (262, 841), (315, 841),
    ),
    "squat": FigurePose(
        (288, 208), (288, 289), (244, 319), (332, 319), (198, 398), (378, 398),
        (254, 445), (322, 445), (260, 550), (316, 550),
        (176, 610), (400, 610),
        (215, 790), (360, 790),
    ),
    "march": FigurePose(
        (288, 205), (288, 286), (244, 315), (332, 315), (215, 390), (363, 380),
        (192, 470), (382, 442), (262, 507), (315, 507), (260, 670), (378, 496),
        (258, 834), (382, 625),
    ),
    "walk": FigurePose(
        (288, 205), (288, 286), (244, 315), (332, 315), (222, 400), (365, 385),
        (246, 476), (376, 472), (262, 510), (315, 510), (208, 638), (378, 545),
        (174, 804), (430, 715),
    ),
    "balance": FigurePose(
        (288, 205), (288, 286), (244, 315), (332, 315), (201, 394), (382, 394),
        (174, 435), (410, 435), (262, 510), (315, 510), (264, 695), (377, 585),
        (262, 840), (390, 685),
    ),
    # Chão em y≈540: antebraços (prancha) ou mãos (flexão) e pontas dos pés apoiados;
    # corpo em linha reta dos ombros aos tornozelos, afastado do chão.
    "plank": FigurePose(
        (85, 448), (120, 455), (145, 460), (165, 463), (147, 540), (167, 540),
        (72, 540), (95, 540), (300, 497), (325, 500), (408, 517), (432, 520),
        (500, 535), (520, 538), True,
    ),
    "push_up": FigurePose(
        (85, 410), (120, 415), (145, 420), (165, 423), (147, 480), (167, 482),
        (148, 540), (168, 540), (300, 470), (325, 473), (408, 505), (432, 508),
        (500, 535), (520, 538), True,
    ),
    "hip_mobility": FigurePose(
        (288, 205), (288, 286), (244, 315), (332, 315), (215, 388), (361, 388),
        (218, 460), (359, 460), (260, 508), (315, 508), (219, 666), (370, 580),
        (220, 822), (390, 705),
    ),
    "side_stretch": FigurePose(
        (332, 202), (344, 281), (294, 314), (379, 321), (270, 383), (410, 259),
        (244, 465), (444, 163), (270, 516), (322, 526), (261, 681), (321, 683),
        (257, 839), (323, 839),
    ),
    "hamstring_stretch": FigurePose(
        (288, 210), (288, 291), (244, 318), (332, 318), (217, 408), (367, 407),
        (211, 497), (383, 494), (260, 522), (318, 522), (237, 680), (356, 651),
        (238, 837), (420, 760),
    ),
    "seated_posture": FigurePose(
        (278, 205), (278, 283), (234, 312), (322, 312), (199, 422), (366, 422),
        (218, 530), (358, 530), (258, 506), (310, 506), (180, 550), (400, 550),
        (180, 820), (400, 820),
    ),
    # Vista lateral, parede à direita: corpo em diagonal, mãos apoiadas na altura dos ombros.
    "wall_push_up": FigurePose(
        (402, 228), (380, 300), (372, 305), (388, 305), (440, 302), (452, 304),
        (505, 298), (515, 300), (288, 520), (300, 522), (240, 680), (252, 682),
        (196, 840), (208, 842),
    ),
    "wall_push_up_bent": FigurePose(
        (452, 236), (428, 306), (420, 310), (436, 310), (408, 382), (424, 384),
        (505, 298), (515, 300), (300, 525), (312, 527), (246, 682), (258, 684),
        (196, 840), (208, 842),
    ),
    # Rosca: cotovelos colados ao corpo, punhos à altura dos ombros.
    "biceps_curl": FigurePose(
        (288, 226), (288, 301), (244, 331), (332, 331), (236, 430), (340, 430),
        (238, 338), (338, 338), (263, 524), (313, 524), (264, 684), (312, 684),
        (262, 841), (315, 841),
    ),
    # Desenvolvimento, posição inicial: punhos à altura dos ombros, cotovelos abertos.
    "overhead_press_start": FigurePose(
        (288, 226), (288, 301), (244, 331), (332, 331), (200, 400), (376, 400),
        (205, 315), (371, 315), (263, 524), (313, 524), (264, 684), (312, 684),
        (262, 841), (315, 841),
    ),
    "lateral_raise": FigurePose(
        (288, 226), (288, 301), (244, 331), (332, 331), (168, 336), (408, 336),
        (95, 340), (481, 340), (263, 524), (313, 524), (264, 684), (312, 684),
        (262, 841), (315, 841),
    ),
    "dumbbell_press": FigurePose(
        (288, 210), (288, 290), (244, 319), (332, 319), (219, 245), (357, 245),
        (220, 164), (356, 164), (260, 523), (316, 523), (260, 684), (316, 684),
        (258, 839), (319, 839),
    ),
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _point(point: tuple[int, int]) -> tuple[int, int]:
    return point[0] * SCALE, point[1] * SCALE


def _blend_with_white(color: tuple[int, int, int], opacity: int) -> tuple[int, int, int]:
    return tuple((channel * opacity + 255 * (255 - opacity)) // 255 for channel in color)


def _draw_segment(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int],
    width: int,
) -> None:
    first = _point(start)
    second = _point(end)
    outer_width = width * SCALE
    inner_width = max(2, width - 10) * SCALE
    draw.line((first, second), fill=INK, width=outer_width, joint="curve")
    draw.line((first, second), fill=color, width=inner_width, joint="curve")
    radius = inner_width // 2
    for x, y in (first, second):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline=INK, width=2 * SCALE)


def _draw_background(prompt: str) -> Image.Image:
    normalized = _normalize(prompt)
    if any(term in normalized for term in ("home office", "escritorio", "postura", "trabalho")):
        top, bottom, accent = (234, 226, 252), (197, 220, 251), (131, 90, 205)
    elif any(term in normalized for term in ("caminhada", "caminhar", "corrida", "parque", "andar")):
        top, bottom, accent = (198, 235, 251), (228, 246, 207), (41, 158, 135)
    elif any(term in normalized for term in ("alongamento", "flexibilidade", "mobilidade")):
        top, bottom, accent = (255, 224, 210), (255, 238, 181), (235, 111, 75)
    else:
        top, bottom, accent = (192, 230, 251), (233, 240, 255), (32, 145, 176)

    height = FRAME_HEIGHT * SCALE
    width = FRAME_WIDTH * SCALE
    gradient = Image.new("RGB", (1, height))
    pixels = gradient.load()
    for y in range(height):
        amount = y / max(1, height - 1)
        color = tuple(round(top[index] * (1 - amount) + bottom[index] * amount) for index in range(3))
        pixels[0, y] = color
    image = gradient.resize((width, height))

    draw = ImageDraw.Draw(image)
    draw.polygon(
        [_point((18, 18)), _point((FRAME_WIDTH - 18, 18)), _point((FRAME_WIDTH - 18, 155)), _point((18, 280))],
        fill=(255, 255, 255),
    )
    draw.polygon(
        [_point((18, 710)), _point((FRAME_WIDTH - 18, 630)), _point((FRAME_WIDTH - 18, FRAME_HEIGHT - 18)), _point((18, FRAME_HEIGHT - 18))],
        fill=(255, 255, 255),
    )

    center = _point((288, 496))
    for x in range(-100, FRAME_WIDTH + 120, 120):
        draw.line((center, _point((x, 18))), fill=_blend_with_white(accent, 65), width=2 * SCALE)

    for x, y in ((48, 430), (493, 339), (68, 690), (502, 750)):
        for row in range(4):
            for column in range(4):
                dot_x = (x + column * 12) * SCALE
                dot_y = (y + row * 12) * SCALE
                radius = 2 * SCALE
                draw.ellipse((dot_x - radius, dot_y - radius, dot_x + radius, dot_y + radius), fill=accent)

    draw.line((_point((28, 850)), _point((548, 850))), fill=INK, width=4 * SCALE)
    for x in range(48, 548, 100):
        draw.line(
            (_point((x, 852)), _point((x - 23, 876))),
            fill=_blend_with_white(accent, 180),
            width=2 * SCALE,
        )

    if any(term in normalized for term in ("casa", "home", "sem equipamento", "sala")):
        _draw_window(draw, (40, 103), accent)
        _draw_plant(draw, (487, 510), accent)
    if any(term in normalized for term in ("sentar", "sentado", "sentada", "seated posture")):
        _draw_chair(draw, (220, 416), accent)
    elif any(term in normalized for term in ("cadeira", "agachamento", "seated")):
        _draw_chair(draw, (400, 502), accent)
    if any(term in normalized for term in ("escritorio", "trabalho", "postura", "home office")):
        _draw_desk(draw, (40, 535), accent)
    if any(term in normalized for term in ("caminhada", "caminhar", "corrida", "parque", "andar")):
        _draw_tree(draw, (75, 533), accent)
        _draw_tree(draw, (485, 425), accent)
    if any(term in normalized for term in ("prancha", "chao", "colchonete", "tapete", "flexao", "push")):
        draw.rounded_rectangle(
            (36 * SCALE, 802 * SCALE, 540 * SCALE, 835 * SCALE),
            radius=12 * SCALE,
            fill=(69, 183, 169),
            outline=INK,
            width=3 * SCALE,
        )
        for x in range(48, 535, 28):
            draw.line((_point((x, 807)), _point((x + 10, 830))), fill=(145, 229, 209), width=2 * SCALE)

    draw.rounded_rectangle((13 * SCALE, 13 * SCALE, 563 * SCALE, 1011 * SCALE), radius=36 * SCALE, outline=INK, width=7 * SCALE)
    draw.rounded_rectangle((23 * SCALE, 23 * SCALE, 553 * SCALE, 1001 * SCALE), radius=28 * SCALE, outline=accent, width=3 * SCALE)
    return image


def _draw_window(draw: ImageDraw.ImageDraw, origin: tuple[int, int], accent: tuple[int, int, int]) -> None:
    x, y = origin
    box = (x * SCALE, y * SCALE, (x + 126) * SCALE, (y + 141) * SCALE)
    draw.rounded_rectangle(box, radius=12 * SCALE, fill=(255, 250, 227), outline=INK, width=5 * SCALE)
    draw.line((_point((x + 63, y + 4)), _point((x + 63, y + 137))), fill=accent, width=4 * SCALE)
    draw.line((_point((x + 4, y + 70)), _point((x + 122, y + 70))), fill=accent, width=4 * SCALE)
    draw.ellipse(((x + 81) * SCALE, (y + 18) * SCALE, (x + 110) * SCALE, (y + 47) * SCALE), fill=(255, 195, 68))


def _draw_plant(draw: ImageDraw.ImageDraw, origin: tuple[int, int], accent: tuple[int, int, int]) -> None:
    x, y = origin
    draw.rounded_rectangle(((x - 30) * SCALE, (y + 48) * SCALE, (x + 34) * SCALE, (y + 113) * SCALE), radius=10 * SCALE, fill=(235, 128, 86), outline=INK, width=4 * SCALE)
    draw.line((_point((x, y + 50)), _point((x, y - 20))), fill=(42, 111, 78), width=6 * SCALE)
    for dx, dy in ((-25, 4), (23, -8), (-17, -27), (20, -43), (0, -55)):
        draw.ellipse(((x + dx - 16) * SCALE, (y + dy - 8) * SCALE, (x + dx + 16) * SCALE, (y + dy + 8) * SCALE), fill=accent, outline=INK, width=2 * SCALE)


def _draw_chair(draw: ImageDraw.ImageDraw, origin: tuple[int, int], accent: tuple[int, int, int]) -> None:
    x, y = origin
    draw.line((_point((x + 9, y)), _point((x + 9, y + 180))), fill=INK, width=13 * SCALE)
    draw.line((_point((x + 13, y + 94)), _point((x + 117, y + 94))), fill=INK, width=15 * SCALE)
    draw.line((_point((x + 17, y + 99)), _point((x + 17, y + 180))), fill=accent, width=8 * SCALE)
    draw.line((_point((x + 109, y + 101)), _point((x + 132, y + 180))), fill=INK, width=10 * SCALE)
    draw.line((_point((x + 24, y + 179)), _point((x + 139, y + 179))), fill=INK, width=9 * SCALE)


def _draw_desk(draw: ImageDraw.ImageDraw, origin: tuple[int, int], accent: tuple[int, int, int]) -> None:
    x, y = origin
    draw.rounded_rectangle(((x + 4) * SCALE, y * SCALE, (x + 112) * SCALE, (y + 70) * SCALE), radius=8 * SCALE, fill=accent, outline=INK, width=4 * SCALE)
    draw.rounded_rectangle(((x + 23) * SCALE, (y - 63) * SCALE, (x + 88) * SCALE, (y - 8) * SCALE), radius=7 * SCALE, fill=(52, 67, 110), outline=INK, width=4 * SCALE)
    draw.line((_point((x + 58, y - 7)), _point((x + 58, y + 8))), fill=INK, width=5 * SCALE)
    draw.line((_point((x + 58, y + 68)), _point((x + 58, y + 170))), fill=INK, width=9 * SCALE)
    draw.line((_point((x + 12, y + 169)), _point((x + 108, y + 169))), fill=INK, width=9 * SCALE)


def _draw_tree(draw: ImageDraw.ImageDraw, origin: tuple[int, int], accent: tuple[int, int, int]) -> None:
    x, y = origin
    draw.line((_point((x, y)), _point((x, y + 155))), fill=(115, 79, 53), width=16 * SCALE)
    for dx, dy, radius, color in (
        (-27, -25, 40, (57, 168, 113)),
        (20, -34, 43, accent),
        (0, -76, 42, (104, 195, 126)),
    ):
        draw.ellipse(((x + dx - radius) * SCALE, (y + dy - radius) * SCALE, (x + dx + radius) * SCALE, (y + dy + radius) * SCALE), fill=color, outline=INK, width=3 * SCALE)


def _draw_person(draw: ImageDraw.ImageDraw, pose: FigurePose) -> None:
    for start, end, width in (
        (pose.hip_left, pose.knee_left, 49),
        (pose.knee_left, pose.ankle_left, 42),
        (pose.hip_right, pose.knee_right, 49),
        (pose.knee_right, pose.ankle_right, 42),
    ):
        _draw_segment(draw, start, end, PANTS, width)

    for start, end, width in (
        (pose.shoulder_left, pose.elbow_left, 39),
        (pose.shoulder_right, pose.elbow_right, 39),
    ):
        _draw_segment(draw, start, end, SHIRT, width)

    shoulder_left = _point(pose.shoulder_left)
    shoulder_right = _point(pose.shoulder_right)
    hip_left = _point(pose.hip_left)
    hip_right = _point(pose.hip_right)
    neck = _point(pose.neck)
    torso_points = [
        (shoulder_left[0] - 14 * SCALE, shoulder_left[1] - 8 * SCALE),
        (shoulder_right[0] + 14 * SCALE, shoulder_right[1] - 8 * SCALE),
        (hip_right[0] + 18 * SCALE, hip_right[1]),
        (hip_left[0] - 18 * SCALE, hip_left[1]),
    ]
    draw.polygon(torso_points, fill=SHIRT, outline=INK)
    draw.line((neck, _point((pose.neck[0], pose.neck[1] + 44))), fill=SKIN, width=19 * SCALE)
    draw.line((_point(pose.shoulder_left), _point(pose.shoulder_right)), fill=SHIRT_LIGHT, width=6 * SCALE)
    draw.line((_point((pose.hip_left[0], pose.hip_left[1] - 2)), _point((pose.hip_right[0], pose.hip_right[1] - 2))), fill=INK, width=4 * SCALE)

    for start, end in (
        (pose.elbow_left, pose.wrist_left),
        (pose.elbow_right, pose.wrist_right),
    ):
        _draw_segment(draw, start, end, SKIN_LIGHT, 27)
        x, y = _point(end)
        radius = 14 * SCALE
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=SKIN_LIGHT, outline=INK, width=3 * SCALE)

    head_x, head_y = _point(pose.head)
    head_box = (
        head_x - 36 * SCALE,
        head_y - 49 * SCALE,
        head_x + 36 * SCALE,
        head_y + 46 * SCALE,
    )
    draw.ellipse(head_box, fill=SKIN_LIGHT, outline=INK, width=4 * SCALE)
    hair = [
        (head_x - 36 * SCALE, head_y - 6 * SCALE),
        (head_x - 33 * SCALE, head_y - 36 * SCALE),
        (head_x - 13 * SCALE, head_y - 53 * SCALE),
        (head_x + 12 * SCALE, head_y - 51 * SCALE),
        (head_x + 32 * SCALE, head_y - 34 * SCALE),
        (head_x + 37 * SCALE, head_y - 7 * SCALE),
        (head_x + 16 * SCALE, head_y - 24 * SCALE),
        (head_x - 6 * SCALE, head_y - 18 * SCALE),
    ]
    draw.polygon(hair, fill=INK)
    eye_y = head_y + (1 if pose.face_left else -2) * SCALE
    eye_x = head_x + (-13 if pose.face_left else 10) * SCALE
    draw.ellipse((eye_x - 3 * SCALE, eye_y - 3 * SCALE, eye_x + 3 * SCALE, eye_y + 3 * SCALE), fill=INK)
    smile_box = (
        head_x - 10 * SCALE,
        head_y + 15 * SCALE,
        head_x + 8 * SCALE,
        head_y + 25 * SCALE,
    )
    draw.arc(smile_box, start=8, end=160, fill=INK, width=2 * SCALE)
    draw.ellipse((head_x - 38 * SCALE, head_y - 4 * SCALE, head_x - 27 * SCALE, head_y + 13 * SCALE), fill=SKIN, outline=INK, width=2 * SCALE)

    for ankle in (pose.ankle_left, pose.ankle_right):
        x, y = _point(ankle)
        shoe_box = (x - 39 * SCALE, y - 1 * SCALE, x + 34 * SCALE, y + 26 * SCALE)
        draw.rounded_rectangle(shoe_box, radius=11 * SCALE, fill=SOLE, outline=INK, width=4 * SCALE)
        draw.line((x - 29 * SCALE, y + 19 * SCALE, x + 26 * SCALE, y + 19 * SCALE), fill=(87, 189, 195), width=3 * SCALE)


def _draw_motion(draw: ImageDraw.ImageDraw, pose_name: str, pose: FigurePose, accent: tuple[int, int, int]) -> None:
    if pose_name in {"squat", "push_up", "march", "walk", "balance"}:
        anchor = pose.knee_left if pose_name != "balance" else pose.knee_right
        x, y = _point(anchor)
        for offset in range(3):
            y_offset = offset * 15 * SCALE
            draw.line(
                (x - 63 * SCALE, y + y_offset, x - 34 * SCALE, y + y_offset - 11 * SCALE),
                fill=accent,
                width=4 * SCALE,
            )
    for x, y in ((72, 308), (495, 293), (78, 732), (498, 672)):
        px, py = _point((x, y))
        draw.polygon(
            [
                (px, py - 14 * SCALE),
                (px + 5 * SCALE, py - 5 * SCALE),
                (px + 14 * SCALE, py),
                (px + 5 * SCALE, py + 5 * SCALE),
                (px, py + 14 * SCALE),
                (px - 5 * SCALE, py + 5 * SCALE),
                (px - 14 * SCALE, py),
                (px - 5 * SCALE, py - 5 * SCALE),
            ],
            fill=accent,
        )


def _draw_dumbbells(draw: ImageDraw.ImageDraw, pose: FigurePose, accent: tuple[int, int, int]) -> None:
    for wrist in (pose.wrist_left, pose.wrist_right):
        x, y = _point(wrist)
        draw.line((x - 30 * SCALE, y, x + 30 * SCALE, y), fill=INK, width=7 * SCALE)
        for side in (-1, 1):
            weight_x = x + side * 34 * SCALE
            draw.rounded_rectangle(
                (
                    weight_x - 9 * SCALE,
                    y - 19 * SCALE,
                    weight_x + 9 * SCALE,
                    y + 19 * SCALE,
                ),
                radius=4 * SCALE,
                fill=accent,
                outline=INK,
                width=3 * SCALE,
            )


def render_comic_scene(*, visual_prompt: str, pose_name: str, destination: Path) -> None:
    if pose_name not in POSES:
        valid_poses = ", ".join(sorted(POSES))
        raise ValueError(f"pose deve ser um dos seguintes valores: {valid_poses}")
    if not visual_prompt.strip():
        raise ValueError("visual_prompt não pode ficar vazio.")

    image = _draw_background(visual_prompt)
    draw = ImageDraw.Draw(image)
    pose = POSES[pose_name]
    _draw_motion(draw, pose_name, pose, (239, 109, 60))
    _draw_person(draw, pose)
    if pose_name == "dumbbell_press":
        _draw_dumbbells(draw, pose, (239, 109, 60))
    final = image.resize((FRAME_WIDTH, FRAME_HEIGHT), Image.Resampling.LANCZOS).convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    final.save(destination, format="PNG", optimize=True)


OPENPOSE_COLORS = (
    (255, 0, 0),
    (255, 85, 0),
    (255, 170, 0),
    (255, 255, 0),
    (170, 255, 0),
    (85, 255, 0),
    (0, 255, 0),
    (0, 255, 85),
    (0, 255, 170),
    (0, 255, 255),
    (0, 170, 255),
    (0, 85, 255),
    (0, 0, 255),
    (85, 0, 255),
    (170, 0, 255),
    (255, 0, 255),
    (255, 0, 170),
    (255, 0, 85),
)

OPENPOSE_LIMBS = (
    (1, 2), (2, 3), (3, 4),
    (1, 5), (5, 6), (6, 7),
    (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13),
    (1, 0), (0, 14), (14, 16),
    (0, 15), (15, 17),
)


def render_openpose_guide(*, pose_name: str, destination: Path) -> None:
    if pose_name not in POSES:
        valid_poses = ", ".join(sorted(POSES))
        raise ValueError(f"pose deve ser um dos seguintes valores: {valid_poses}")

    pose = POSES[pose_name]
    center_hip = (
        (pose.hip_left[0] + pose.hip_right[0]) // 2,
        (pose.hip_left[1] + pose.hip_right[1]) // 2,
    )
    points = (
        pose.head,
        pose.neck,
        pose.shoulder_left,
        pose.elbow_left,
        pose.wrist_left,
        pose.shoulder_right,
        pose.elbow_right,
        pose.wrist_right,
        pose.hip_left,
        pose.knee_left,
        pose.ankle_left,
        pose.hip_right,
        pose.knee_right,
        pose.ankle_right,
        # Olhos e orelhas na mesma altura, acima do nariz: cabeça neutra.
        # Orelhas abaixo dos olhos fazem o ControlNet desenhar o rosto olhando para cima.
        (pose.head[0] - 9, pose.head[1] - 10),
        (pose.head[0] + 9, pose.head[1] - 10),
        (pose.head[0] - 20, pose.head[1] - 9),
        (pose.head[0] + 20, pose.head[1] - 9),
    )
    if pose_name in {"plank", "push_up"}:
        width, height = 896, 512
        padding = 110
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        scale = min(
            (width - 2 * padding) / (max_x - min_x),
            (height - 2 * padding) / (max_y - min_y),
        )
        offset_x = (width - (max_x - min_x) * scale) / 2 - min_x * scale
        offset_y = (height - (max_y - min_y) * scale) / 2 - min_y * scale
        scaled = tuple(
            (round(x * scale + offset_x), round(y * scale + offset_y))
            for x, y in points
        )
        line_width = max(8, round(8 * scale))
        point_radius = max(8, round(8 * scale))
    else:
        width, height = 512, 896
        scale_x, scale_y = width / FRAME_WIDTH, height / FRAME_HEIGHT
        scaled = tuple((round(x * scale_x), round(y * scale_y)) for x, y in points)
        line_width = 8
        point_radius = 8
    guide = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(guide)

    for index, (start, end) in enumerate(OPENPOSE_LIMBS):
        draw.line((scaled[start], scaled[end]), fill=OPENPOSE_COLORS[index], width=line_width)
    for index, point in enumerate(scaled):
        x, y = point
        radius = point_radius if index < 14 else max(5, round(point_radius * 0.625))
        color = OPENPOSE_COLORS[index % len(OPENPOSE_COLORS)]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    destination.parent.mkdir(parents=True, exist_ok=True)
    guide.save(destination, format="PNG", optimize=True)
