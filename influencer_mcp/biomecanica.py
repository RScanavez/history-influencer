"""Esqueleto 3D por ângulos articulares (pedido do usuário, 2026-10-06).

Os exercícios saíam errados porque a posição do corpo vinha de texto vago ("one knee lifted") e de esqueletos 2D
desenhados à mão: de perfil, as duas pernas e os dois braços viravam uma linha só; de frente, o que vai para a
frente ou para trás (marcha, rosca, agachamento, calcanhar) não aparecia. Agora cada posição é descrita por ângulos
de cada articulação e tudo sai dela:

- o esqueleto: cinemática direta com as proporções do corpo humano (fração da altura), apoiado no chão pelos pés
  que sustentam o peso, girado para a câmera (de frente, três-quartos ou de perfil) e projetado no quadro;
- o texto para a imagem: onde está cada membro (ângulo, direção, altura da mão e do pé);
- o texto para o vídeo: o que cada articulação faz, de quanto a quanto, e o que fica parado;
- o vídeo de esqueleto (Fun Control): interpolado nos ângulos, então os membros não encolhem no meio do movimento.

Coordenadas: x = lado esquerdo da pessoa, y = para cima, z = para a frente da pessoa; unidade = altura do corpo.
"r"/"l" são os lados DA PESSOA. A câmera vê a pessoa girada `yaw` graus: 0 = de frente; positivo = a pessoa vira
para a direita da imagem, mostrando o lado direito dela (o mais perto da câmera).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Sequence

Vec = tuple[float, float, float]
Point = tuple[float, float] | None

# Proporções (fração da altura; Drillis e Contini, arredondadas).
SHOULDER_H = 0.818
HALF_SHOULDER = 0.118
UPPER_ARM = 0.186
FOREARM = 0.146
HIP_H = 0.530
HALF_HIP = 0.085
THIGH = 0.245
SHANK = 0.246
ANKLE_H = 0.039
FOOT = 0.152
TORSO = SHOULDER_H - HIP_H
HEAD = 0.117  # do meio dos ombros ao nariz
BODY_CM = 160  # para dizer alturas em centímetros (mulher de 60+)

# Quadro de referência 576x1024 (o mesmo de illustration.POSES): sola dos pés em y=868, ~686 px de altura.
REF_W, REF_H = 576, 1024
SCALE = 686
GROUND_Y = 868
CENTER_X = 288


@dataclass(frozen=True)
class Arm:
    elevation: float = 5  # ângulo do braço com o tronco (0 = pendurado, 90 = horizontal, 180 = para cima)
    plane: float = 0  # direção da elevação: 0 = para o lado, 90 = para a frente, -90 = para trás
    elbow: float = 5  # flexão do cotovelo (0 = esticado)
    bend: str = "forward"  # para onde o antebraço dobra: forward, up, in, down


@dataclass(frozen=True)
class Leg:
    flex: float = 0  # coxa para a frente (graus; negativo = para trás)
    abduction: float = 0  # perna aberta para o lado
    knee: float = 0  # flexão do joelho
    heel: float = 0  # calcanhar levantado (graus do pé com o chão)


@dataclass(frozen=True)
class Pose:
    arm_r: Arm = Arm()
    arm_l: Arm = Arm()
    leg_r: Leg = Leg()
    leg_l: Leg = Leg()
    trunk_flex: float = 0  # tronco inclinado para a frente
    trunk_side: float = 0  # tronco inclinado para a esquerda da pessoa (negativo = direita)
    lean: float = 0  # corpo inteiro inclinado para a frente sobre os pés (flexão na parede)
    support: tuple[str, ...] = ("r", "l")  # pés que sustentam o peso
    seated: bool = False  # sentada: o quadril fica na cadeira (calcanhar levantado sobe só o joelho, não o corpo)
    contacts: dict[str, str] = field(default_factory=dict)  # "hand_l": "resting on the top of a chair back"


SIDE_SIGN = {"r": -1.0, "l": 1.0}
SIDE_NAME = {"r": "right", "l": "left"}


def _add(a: Vec, b: Vec, k: float = 1.0) -> Vec:
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _rot_x(v: Vec, deg: float) -> Vec:
    """Gira no plano sagital: +y vai para +z (inclinar para a frente)."""
    t = math.radians(deg)
    return (v[0], v[1] * math.cos(t) - v[2] * math.sin(t), v[1] * math.sin(t) + v[2] * math.cos(t))


def _rot_z(v: Vec, deg: float) -> Vec:
    """Gira no plano frontal: -y vai para +x (abrir para a esquerda da pessoa)."""
    t = math.radians(deg)
    return (v[0] * math.cos(t) - v[1] * math.sin(t), v[0] * math.sin(t) + v[1] * math.cos(t), v[2])


def _trunk(v: Vec, pose: Pose) -> Vec:
    return _rot_z(_rot_x(v, pose.trunk_flex), -pose.trunk_side)


def _arm_vectors(arm: Arm, side: str, pose: Pose) -> tuple[Vec, Vec]:
    s = SIDE_SIGN[side]
    e, p = math.radians(arm.elevation), math.radians(arm.plane)
    upper = (math.sin(e) * math.cos(p) * s, -math.cos(e), math.sin(e) * math.sin(p))
    hint = {"forward": (0.0, 0.0, 1.0), "up": (0.0, 1.0, 0.0), "in": (-s, 0.0, 0.0), "down": (0.0, -1.0, 0.0)}[arm.bend]
    perp = _sub(hint, tuple(c * _dot(hint, upper) for c in upper))  # type: ignore[arg-type]
    if _dot(perp, perp) < 1e-6:
        perp = _sub((0.0, 1.0, 0.0), tuple(c * upper[1] for c in upper))  # type: ignore[arg-type]
    perp = _norm(perp)
    k = math.radians(arm.elbow)
    fore = _add(tuple(c * math.cos(k) for c in upper), perp, math.sin(k))  # type: ignore[arg-type]
    return _trunk(upper, pose), _trunk(fore, pose)


def _leg_vectors(leg: Leg, side: str) -> tuple[Vec, Vec]:
    s = SIDE_SIGN[side]
    f, k = math.radians(leg.flex), math.radians(leg.flex - leg.knee)
    thigh = (0.0, -math.cos(f), math.sin(f))
    shank = (0.0, -math.cos(k), math.sin(k))
    return _rot_z(thigh, s * leg.abduction), _rot_z(shank, s * leg.abduction)


def _heel_lift(heel: float) -> float:
    """Quanto o tornozelo sobe com o calcanhar levantado (o pé gira na ponta)."""
    return FOOT * 0.75 * math.sin(math.radians(heel))


def reach(pose: Pose, side: str, target: Vec, plane: float, bend: str) -> Arm:
    """Braço (elevação e cotovelo, no plano dado) que leva a mão o mais perto possível de target: a mão fica no
    lugar (na parede, por exemplo) enquanto o corpo se move."""
    best: tuple[float, Arm] | None = None
    for elevation in range(0, 181, 2):
        for elbow in range(0, 151, 2):
            arm = Arm(elevation, plane, elbow, bend)
            wrist = skeleton(replace(pose, **{f"arm_{side}": arm}))[f"wrist_{side}"]
            gap = _dot(_sub(wrist, target), _sub(wrist, target))
            if best is None or gap < best[0]:
                best = (gap, arm)
    assert best is not None
    return best[1]


JOINT_NAMES = ("nose", "neck", "shoulder_r", "elbow_r", "wrist_r", "shoulder_l", "elbow_l", "wrist_l",
               "hip_r", "knee_r", "ankle_r", "hip_l", "knee_l", "ankle_l", "eye_r", "eye_l", "ear_r", "ear_l")


def skeleton(pose: Pose) -> dict[str, Vec]:
    """Pontos 3D (unidade = altura do corpo), com os pés de apoio no chão no lugar de sempre."""
    pelvis: Vec = (0.0, 0.0, 0.0)
    spine = _trunk((0.0, 1.0, 0.0), pose)
    lateral = _trunk((1.0, 0.0, 0.0), pose)
    forward = _trunk((0.0, 0.0, 1.0), pose)
    neck = _add(pelvis, spine, TORSO)
    head = _add(neck, spine, HEAD)
    points: dict[str, Vec] = {"neck": neck, "nose": _add(head, forward, 0.045)}
    for side in ("r", "l"):
        s = SIDE_SIGN[side]
        shoulder = _add(neck, lateral, s * HALF_SHOULDER)
        upper, fore = _arm_vectors(getattr(pose, f"arm_{side}"), side, pose)
        elbow = _add(shoulder, upper, UPPER_ARM)
        hip = (s * HALF_HIP, 0.0, 0.0)
        thigh, shank = _leg_vectors(getattr(pose, f"leg_{side}"), side)
        knee = _add(hip, thigh, THIGH)
        eye = _add(_add(_add(head, forward, 0.035), lateral, s * 0.03), spine, 0.015)
        points.update({f"shoulder_{side}": shoulder, f"elbow_{side}": elbow, f"wrist_{side}": _add(elbow, fore, FOREARM),
                       f"hip_{side}": hip, f"knee_{side}": knee, f"ankle_{side}": _add(knee, shank, SHANK),
                       f"eye_{side}": eye, f"ear_{side}": _add(_add(head, lateral, s * 0.07), forward, -0.01)})
    # Apoio: o primeiro pé de apoio vai para o seu lugar no chão (pés afastados na largura do quadril); com o
    # calcanhar levantado o corpo sobe (o pé gira na ponta).
    side = pose.support[0]
    heel = 0.0 if pose.seated else max(getattr(pose, f"leg_{s}").heel for s in pose.support)
    target = (SIDE_SIGN[side] * HALF_HIP, ANKLE_H + _heel_lift(heel), 0.0)
    shift = _sub(target, points[f"ankle_{side}"])
    points = {name: _add(value, shift) for name, value in points.items()}
    if pose.seated:
        # Calcanhar levantado sentada: o tornozelo sobe e o joelho acompanha girando a coxa no quadril
        # (comprimento da coxa preservado).
        for leg_side in ("r", "l"):
            lift = _heel_lift(getattr(pose, f"leg_{leg_side}").heel)
            if not lift:
                continue
            hip, knee, ankle = (points[f"{j}_{leg_side}"] for j in ("hip", "knee", "ankle"))
            shank = _sub(ankle, knee)
            rise = knee[1] + lift - hip[1]
            flat = math.sqrt(max(THIGH ** 2 - rise ** 2, 0.0))
            horizontal = _norm((knee[0] - hip[0], 0.0, knee[2] - hip[2]))
            new_knee = (hip[0] + horizontal[0] * flat, hip[1] + rise, hip[2] + horizontal[2] * flat)
            points[f"knee_{leg_side}"] = new_knee
            points[f"ankle_{leg_side}"] = _add(new_knee, shank)
    if pose.lean:
        anchor = target
        points = {name: _add(anchor, _rot_x(_sub(value, anchor), pose.lean)) for name, value in points.items()}
    return points


def view_of(yaw: float) -> str:
    return "front" if abs(yaw) < 15 else ("side" if abs(yaw) >= 75 else "three_quarter")


def project(points3d: dict[str, Vec], yaw: float, offset_x: float = 0.0) -> list[Point]:
    """18 pontos OpenPose em pixels do quadro de referência; olhos e orelhas virados para longe ficam de fora."""
    t = math.radians(yaw)

    def screen(v: Vec) -> tuple[float, float, float]:
        x = v[0] * math.cos(t) + v[2] * math.sin(t)
        depth = -v[0] * math.sin(t) + v[2] * math.cos(t)
        return CENTER_X + offset_x + x * SCALE, GROUND_Y - v[1] * SCALE, depth

    head_depth = screen(_add(points3d["nose"], (0.0, 0.0, 0.0)))[2] - 0.045 * math.cos(t)
    out: list[Point] = []
    for name in JOINT_NAMES:
        x, y, depth = screen(points3d[name])
        if name.startswith(("eye", "ear")) and depth < head_depth - (0.0 if name.startswith("eye") else 0.02):
            out.append(None)  # do outro lado da cabeça: a câmera não vê
        else:
            out.append((x, y))
    return out


def centered_offset(points: Sequence[Point]) -> float:
    xs = [p[0] for p in points if p is not None]
    return CENTER_X - (min(xs) + max(xs)) / 2


def interpolate_pose(a: Pose, b: Pose, t: float) -> Pose:
    """Pose intermediária nos ângulos (não nos pontos): os membros mantêm o comprimento."""
    def mix(x: float, y: float) -> float:
        return x + (y - x) * t

    def arm(p: Arm, q: Arm) -> Arm:
        return Arm(mix(p.elevation, q.elevation), mix(p.plane, q.plane), mix(p.elbow, q.elbow), q.bend if t > 0.5 else p.bend)

    def leg(p: Leg, q: Leg) -> Leg:
        return Leg(mix(p.flex, q.flex), mix(p.abduction, q.abduction), mix(p.knee, q.knee), mix(p.heel, q.heel))

    support = b.support if t > 0.5 else a.support
    if set(a.support) & set(b.support):  # mantém no chão o pé que fica apoiado do começo ao fim
        support = tuple(s for s in a.support if s in b.support)
    return replace(a, arm_r=arm(a.arm_r, b.arm_r), arm_l=arm(a.arm_l, b.arm_l), leg_r=leg(a.leg_r, b.leg_r),
                   leg_l=leg(a.leg_l, b.leg_l), trunk_flex=mix(a.trunk_flex, b.trunk_flex),
                   trunk_side=mix(a.trunk_side, b.trunk_side), lean=mix(a.lean, b.lean), support=support,
                   seated=a.seated if t <= 0.5 else b.seated)


# ---------------------------------------------------------------- descrição em texto (inglês, para os modelos)

def _round5(value: float) -> int:
    return int(5 * round(value / 5))


def _height_word(y: float, points: dict[str, Vec]) -> str:
    """Altura de um ponto comparada com o corpo NA POSE ATUAL (no agachamento, o ombro está mais baixo)."""
    shoulder = (points["shoulder_r"][1] + points["shoulder_l"][1]) / 2
    hip = (points["hip_r"][1] + points["hip_l"][1]) / 2
    knee = (points["knee_r"][1] + points["knee_l"][1]) / 2
    nose = points["nose"][1]
    torso = shoulder - hip
    for limit, word in ((nose + 0.06, "above the head"), (nose - 0.04, "at head height"),
                        (shoulder - 0.04, "at shoulder height"), (shoulder - torso * 0.45, "at chest height"),
                        (hip + torso * 0.2, "at waist height"), (hip - 0.06, "at hip height"),
                        (knee + 0.05, "at thigh height"), (knee - 0.05, "at knee height")):
        if y >= limit:
            return word
    return "low, near the shins"


def _segment(v: Vec, side: str) -> str:
    """Direção de um segmento no espaço (não em relação ao tronco): o que a câmera vê."""
    angle = math.degrees(math.acos(max(-1.0, min(1.0, -v[1]))))  # 0 = para baixo, 90 = horizontal, 180 = para cima
    if angle < 20:
        return "pointing straight down"
    if angle > 160:
        return "pointing straight up"
    level = "horizontal" if 75 <= angle <= 105 else ("angled upward" if angle > 105 else "angled downward")
    where = " and ".join(w for w in _direction(v, side).split(" and ") if w not in ("up", "down"))
    return f"pointing {where or 'forward'}, {level}"


def _direction(v: Vec, side: str) -> str:
    s = SIDE_SIGN[side]
    lateral, up, fwd = v[0] * s, v[1], v[2]
    parts = []
    if up > 0.5:
        parts.append("up")
    elif up < -0.5:
        parts.append("down")
    if fwd > 0.35:
        parts.append("forward")
    elif fwd < -0.35:
        parts.append("backward")
    if lateral > 0.35:
        parts.append("out to the side")
    elif lateral < -0.35:
        parts.append("across the body")
    return " and ".join(parts) or "forward"


def describe_arm(pose: Pose, points: dict[str, Vec], side: str) -> str:
    arm: Arm = getattr(pose, f"arm_{side}")
    name = SIDE_NAME[side]
    contact = pose.contacts.get(f"hand_{side}")
    shoulder, elbow, wrist = (points[f"{j}_{side}"] for j in ("shoulder", "elbow", "wrist"))
    upper = _norm(_sub(elbow, shoulder))
    fore = _norm(_sub(wrist, elbow))
    if -upper[1] > 0.94:
        upper_text = "upper arm hanging down alongside the body"
    elif upper[1] > 0.94:
        upper_text = "arm raised straight up overhead"
    else:
        upper_text = f"upper arm {_segment(upper, side)}"
    elbow_text = "elbow straight" if arm.elbow < 15 else (
        f"elbow bent about {_round5(arm.elbow)} degrees, forearm {_segment(fore, side)}")
    hand = f"hand {_height_word(wrist[1], points)}"
    if contact:
        hand += f", {contact}"
    return f"{name} arm: {upper_text}, {elbow_text}, {hand}"


def describe_leg(pose: Pose, points: dict[str, Vec], side: str) -> str:
    leg: Leg = getattr(pose, f"leg_{side}")
    name = SIDE_NAME[side]
    ankle = points[f"ankle_{side}"]
    if pose.seated and 75 <= leg.flex <= 105:
        thigh = "thigh resting horizontally on the chair seat"
    elif leg.flex >= 10:
        thigh = (f"thigh raised forward about {_round5(leg.flex)} degrees"
                 + (" (horizontal, knee at hip height)" if 75 <= leg.flex <= 105 else ""))
    elif leg.flex <= -10:
        thigh = f"thigh reaching back about {_round5(-leg.flex)} degrees"
    else:
        thigh = "thigh vertical under the hip"
    if leg.abduction >= 10:
        thigh += f", whole leg opened out to the side about {_round5(leg.abduction)} degrees"
    knee = "knee straight" if leg.knee < 12 else f"knee bent about {_round5(leg.knee)} degrees"
    lift_cm = (ankle[1] - ANKLE_H) * BODY_CM
    if side in pose.support:
        foot = ("foot flat on the floor" if leg.heel < 10
                else f"heel lifted about {_round5(lift_cm)} cm, weight on the ball of the foot")
    else:
        foot = f"foot off the floor, about {max(5, _round5(lift_cm))} cm up"
    return f"{name} leg: {thigh}, {knee}, {foot}"


def describe_trunk(pose: Pose) -> str:
    parts = []
    if pose.lean >= 8:
        parts.append(f"whole body straight like a plank, leaning forward about {_round5(pose.lean)} degrees from the feet")
    if pose.trunk_flex >= 8:
        parts.append(f"torso leaning forward about {_round5(pose.trunk_flex)} degrees with a straight back")
    if abs(pose.trunk_side) >= 8:
        parts.append(f"torso bending toward the {'left' if pose.trunk_side > 0 else 'right'} side about "
                     f"{_round5(abs(pose.trunk_side))} degrees")
    return ", ".join(parts) or "torso upright, back straight"


def view_text(yaw: float) -> str:
    view = view_of(yaw)
    if view == "front":
        return "front view, facing the camera, face clearly visible"
    if view == "side":
        return "side view in profile, facing the right side of the image, face in profile"
    return f"three-quarter view, body turned about {_round5(abs(yaw))} degrees to the side, face visible"


def standing(pose: Pose) -> bool:
    return not pose.seated


def posture_text(pose: Pose) -> str:
    """Postura geral primeiro: com "chair" no prompt o SDXL sentava a personagem nos exercícios em pé."""
    if pose.seated:
        return "sitting upright on a sturdy chair"
    if pose.lean >= 8:
        return "standing at arm's length from a wall, leaning on it with straight body, not sitting"
    if all(getattr(pose, f"leg_{side}").knee >= 30 for side in pose.support):
        return "in a half squat on both feet, hips pushed back, not sitting, nothing under the hips"
    if len(pose.support) == 1:
        return f"standing upright on the {SIDE_NAME[pose.support[0]]} leg only, not sitting"
    return "standing upright on both feet, not sitting"


def _neutral_arm(pose: Pose, side: str) -> bool:
    arm: Arm = getattr(pose, f"arm_{side}")
    return arm.elevation < 15 and arm.elbow < 15 and f"hand_{side}" not in pose.contacts and pose.trunk_flex < 15


def _neutral_leg(pose: Pose, side: str) -> bool:
    leg: Leg = getattr(pose, f"leg_{side}")
    return (not pose.seated and side in pose.support and leg.flex < 10 and leg.abduction < 10 and leg.knee < 12
            and leg.heel < 10)


def describe(pose: Pose, yaw: float) -> str:
    """Posição para a imagem-chave: postura, tronco e só os membros que fazem algo (os demais num resumo curto),
    para a roupa e os objetos do roteiro não se perderem no fim de um prompt longo."""
    points = skeleton(pose)
    parts = [view_text(yaw), posture_text(pose)]
    trunk = describe_trunk(pose)
    if trunk != "torso upright, back straight":
        parts.append(trunk)
    relaxed_arms = [side for side in ("r", "l") if _neutral_arm(pose, side)]
    parts += [describe_arm(pose, points, side) for side in ("r", "l") if side not in relaxed_arms]
    if pose.contacts.get("hands"):
        parts.append(pose.contacts["hands"])
    if len(relaxed_arms) == 2:
        parts.append("both arms relaxed at the sides")
    elif relaxed_arms:
        parts.append(f"{SIDE_NAME[relaxed_arms[0]]} arm relaxed at the side")
    plain_legs = [side for side in ("r", "l") if _neutral_leg(pose, side)]
    parts += [describe_leg(pose, points, side) for side in ("r", "l") if side not in plain_legs]
    if len(plain_legs) == 2:
        parts.append("both legs straight, feet flat on the floor hip-width apart")
    elif plain_legs:
        parts.append(f"{SIDE_NAME[plain_legs[0]]} leg straight, foot flat on the floor")
    return "; ".join(parts)


# ---------------------------------------------------------------- descrição do movimento (para o vídeo)

def _changes(a: Pose, b: Pose) -> list[tuple[str, str, float, float]]:
    """(parte, articulação, de, para) que mudam pelo menos 10 graus."""
    out = []
    for side in ("r", "l"):
        for part, fields in ((f"arm_{side}", ("elevation", "elbow")), (f"leg_{side}", ("flex", "abduction", "knee", "heel"))):
            for name in fields:
                start, end = getattr(getattr(a, part), name), getattr(getattr(b, part), name)
                if abs(end - start) >= 10:
                    out.append((part, name, start, end))
    for name in ("trunk_flex", "trunk_side", "lean"):
        if abs(getattr(b, name) - getattr(a, name)) >= 8:
            out.append(("trunk", name, getattr(a, name), getattr(b, name)))
    return out


# (verbo de ida, verbo de volta, o quê na ida, o quê na volta, quantidade)
_ACTION = {
    ("arm", "elevation"): ("raises", "lowers", "the {side} arm", "the {side} arm", "from {a} to {b} degrees"),
    ("arm", "elbow"): ("bends", "straightens", "the {side} elbow", "the {side} elbow", "from {a} to {b} degrees"),
    ("leg", "flex"): ("lifts", "lowers", "the {side} thigh forward", "the {side} thigh back down",
                      "from {a} to {b} degrees"),
    ("leg", "abduction"): ("opens", "brings", "the {side} leg out to the side", "the {side} leg back in",
                           "from {a} to {b} degrees"),
    ("leg", "knee"): ("bends", "straightens", "the {side} knee", "the {side} knee", "from {a} to {b} degrees"),
    ("leg", "heel"): ("lifts", "lowers", "the {side} heel off the floor", "the {side} heel back to the floor", ""),
    ("trunk", "trunk_flex"): ("leans", "brings", "the torso forward", "the torso back upright",
                              "from {a} to {b} degrees"),
    ("trunk", "trunk_side"): ("bends", "brings", "the torso to the side", "the torso back upright",
                              "from {a} to {b} degrees"),
    ("trunk", "lean"): ("lowers", "pushes", "the chest toward the wall", "the body back away from the wall", ""),
}


def movement(a: Pose, b: Pose) -> str:
    """O que cada articulação faz de a para b, juntando os dois lados quando fazem o mesmo."""
    changes = _changes(a, b)
    phrases: list[str] = []
    done: set[tuple[str, str]] = set()
    for part, name, start, end in changes:
        kind = part.split("_")[0]
        if (kind, name, part) in done or (kind, name, "both") in done:
            continue
        # Junta os dois lados só quando fazem o mesmo movimento (na marcha, cada braço vai para um lado).
        twins = [p for p, n, s, e in changes if p != part and p.split("_")[0] == kind and n == name
                 and abs(s - start) < 5 and abs(e - end) < 5]
        up, down, what_up, what_down, amount = _ACTION[(kind, name)]
        verb, what = (up, what_up) if end > start else (down, what_down)
        side = "both" if twins else (SIDE_NAME[part[-1]] if kind != "trunk" else "")
        what = what.format(side=side).replace("the both ", "both ").replace("  ", " ")
        if (kind, name) == ("arm", "elevation"):
            plane = getattr(b if end > start else a, part).plane
            what += " backward" if plane < -45 else (" forward" if plane > 45 else " out to the side")
        if side == "both":
            what = what.replace("elbow", "elbows").replace("arm ", "arms ").replace("heel", "heels") \
                .replace("knee", "knees").replace("thigh", "thighs").replace("leg ", "legs ")
            what = what + "s" if what.endswith((" arm", " leg")) else what
        phrases.append(f"{verb} {what} {amount.format(a=_round5(start), b=_round5(end))}".strip())
        done.add((kind, name, "both" if twins else part))
    return ", ".join(phrases)


def still_parts(a: Pose, b: Pose) -> str:
    moving = {part for part, *_ in _changes(a, b)}
    still = []
    if "trunk" not in moving:
        still.append("the torso stays upright and still")
    planted = [side for side in ("r", "l") if f"leg_{side}" not in moving and side in a.support and side in b.support]
    if len(planted) == 2:
        still.append("both feet stay flat on the floor")
    elif planted:
        still.append(f"the {SIDE_NAME[planted[0]]} foot stays flat on the floor, bearing the weight")
    for side in ("r", "l"):
        if f"arm_{side}" not in moving and a.contacts.get(f"hand_{side}"):
            still.append(f"the {SIDE_NAME[side]} hand stays {a.contacts[f'hand_{side}']}")
    if not any(p.startswith("arm") for p in moving) and not any(k.startswith("hand") for k in a.contacts):
        still.append("the arms stay still")
    return ", ".join(still)
