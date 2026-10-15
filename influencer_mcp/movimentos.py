"""Catálogo de movimentos descritos em texto a partir de manuais (pedido do usuário, 2026-10-08).

A biblioteca de esqueletos (exercicios.py) foi desligada em vídeos novos porque os resultados não ficaram bons. Em
vez dela, cada exercício é escrito com a posição inicial, as duas fases do movimento e os pontos de execução, com
linguagem de manuais de educação física, CrossFit e cinesiologia:

- NSCA, "Exercise Technique Manual for Resistance Training": posição de partida, fases e erros comuns;
- CrossFit Level 1 Training Guide e crossfit.com/essentials: pontos de performance ("heels down", "lumbar curve
  maintained", "knees track the toes", "front shin vertical", "full hip and knee extension");
- cinesiologia (NASM, ACE): plano, ações articulares e fase concêntrica/excêntrica de cada movimento.

Uma cena usa `"movement": "<id>"`. O sistema acrescenta ao `keyframePrompt` a posição inicial detalhada e troca os
`motions` por uma descrição das duas fases (primeira metade e volta), com ritmo e pontos de execução. Os textos em
inglês vão para o SDXL e o Wan; os em português são para o roteirista. As profundidades e cargas são as de 60+: sem
agachar abaixo do paralelo, sem carga pesada, sem apoio: nenhum movimento usa parede, cadeira, degrau ou corrimão (decisão do usuário, 2026-10-08).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TEMPO = "slow and controlled, about two seconds for each phase, smooth and steady, no jerking or bouncing"
CAMERA = "Static camera, same framing, no cuts. Limbs keep their natural length and joints bend naturally"


@dataclass(frozen=True)
class Movement:
    id: str
    label: str  # português, para o roteirista e a folha de revisão
    view: str  # câmera
    setup: str  # posição inicial (inglês, para a imagem)
    go: str  # primeira fase (inglês)
    back: str  # volta (inglês)
    cues: tuple[str, ...]  # pontos de execução (inglês, entram no prompt do vídeo)
    kinesiology: str  # português: plano, ações articulares, músculos
    safety: str  # português
    standing: bool = True


MOVEMENTS: dict[str, Movement] = {m.id: m for m in (
    Movement(
        "bodyweight_squat", "Agachamento livre (profundidade moderada)", "three-quarter side view",
        "standing tall with feet shoulder-width apart and toes turned slightly outward, heels flat on the floor, chest up, "
        "spine neutral with its natural lower-back curve, head neutral, arms hanging relaxed at the sides",
        "pushes the hips back and down while bending the hips and knees together, knees tracking in line with the toes, "
        "heels staying flat on the floor, torso leaning forward slightly with the back straight, arms swinging forward to "
        "shoulder height for balance, lowering until the thighs are about parallel to the floor and never lower, then "
        "pausing for a moment",
        "drives through the whole foot and extends the hips and knees together to rise back to a full standing position, "
        "arms lowering back to the sides, chest staying up",
        ("knees track over the toes and never cave inward", "heels stay down", "lower back keeps its natural curve, no rounding",
         "eyes forward, head neutral"),
        "Plano sagital. Descida (excêntrica): flexão de quadril e joelho e dorsiflexão do tornozelo; subida (concêntrica): "
        "extensão das três articulações. Glúteo máximo e quadríceps, com isquiotibiais e eretores da espinha.",
        "Só até a coxa ficar paralela ao chão. Joelhos não caem para dentro, calcanhares no chão. Parar se houver dor."),
    Movement(
        "reverse_lunge", "Afundo para trás", "side view",
        "standing tall with feet hip-width apart, both hands on the hips for balance, chest up, spine neutral, eyes "
        "forward",
        "steps one foot straight back about one long stride, landing on the ball of the foot, then bends both knees together "
        "and lowers the hips straight down until the front knee is bent about 90 degrees with the front shin vertical and "
        "the back knee hovers just above the floor, torso upright",
        "pushes through the front heel and straightens both legs, bringing the back foot forward to stand tall with the "
        "feet together and hips and knees fully extended",
        ("front shin vertical, front knee over the ankle", "torso upright, hips level", "back knee lowers toward the floor "
         "without hitting it", "front heel stays down"),
        "Plano sagital. Quadril e joelho da frente flexionam e estendem; o quadril de trás estende. Quadríceps, glúteo "
        "máximo e isquiotibiais; estabilizadores do quadril no equilíbrio.",
        "Mãos na cintura; passo curto e profundidade pequena se faltar equilíbrio; joelho da frente não passa "
        "da ponta do pé."),
    Movement(
        "hip_hinge", "Dobradiça de quadril", "side view",
        "standing tall with feet hip-width apart, knees softly bent about 15 degrees, hands resting on the front of the "
        "thighs, chest up, spine neutral",
        "pushes the hips straight back as if closing a door with the buttocks, the torso tipping forward from the hips with "
        "a flat straight back from head to tailbone, hands sliding down the thighs toward the knees, until the torso is "
        "about 45 to 60 degrees from vertical, knees staying only slightly bent",
        "squeezes the glutes and drives the hips forward to rise back to a tall standing position, the spine staying flat "
        "and the hands sliding back up the thighs",
        ("spine neutral from head to tailbone, no rounding", "knees only slightly bent, shins nearly vertical",
         "the movement comes from the hips, not the back", "weight stays over the middle of the feet"),
        "Plano sagital. Flexão e extensão de quadril com a coluna neutra (padrão do levantamento terra romeno). "
        "Isquiotibiais, glúteo máximo e eretores da espinha.",
        "Sem carga e só até 45 a 60 graus de inclinação. Coluna nunca arredonda; parar se sentir dor lombar."),
    Movement(
        "hip_extension", "Extensão de quadril em pé", "side view",
        "standing tall with feet hip-width apart, both hands on the hips, torso upright, spine neutral, eyes forward",
        "keeps the knee straight and slowly lifts one leg straight back about 20 to 30 degrees from vertical, squeezing the "
        "buttock, the torso staying upright without arching the lower back",
        "lowers the leg slowly back to stand beside the other foot",
        ("knee stays straight", "torso upright, no arching of the lower back", "hips stay square and level",
         "supporting leg slightly soft, not locked"),
        "Plano sagital. Extensão de quadril. Glúteo máximo e isquiotibiais, com estabilização do tronco.",
        "Mãos na cintura; amplitude pequena para a lombar não arquear."),
    Movement(
        "hip_abduction", "Abertura lateral da perna", "front view",
        "standing tall facing the camera with feet together, both hands on the hips, toes pointing forward, torso "
        "upright",
        "keeps the knee straight and the toes pointing forward and lifts the outer leg out to the side about 30 degrees, "
        "the torso staying upright without leaning",
        "lowers the leg slowly back to stand with the feet together",
        ("toes point forward, not up", "torso upright, no leaning", "hips level", "movement slow, no swinging"),
        "Plano frontal. Abdução de quadril. Glúteo médio e mínimo, tensor da fáscia lata; estabiliza a outra perna.",
        "Mãos na cintura; amplitude de até cerca de 30 graus; sem balançar o tronco."),
    Movement(
        "calf_raise", "Elevação de calcanhar em pé", "three-quarter side view",
        "standing tall with feet hip-width apart, arms relaxed at the sides, torso upright, knees straight but not "
        "locked, eyes forward",
        "rises onto the balls of both feet, lifting the heels as high as comfortable with the knees straight, and holds "
        "for a moment at the top",
        "lowers the heels slowly back to the floor",
        ("knees stay straight", "weight over the big toe, ankles do not roll outward", "rise and lower slowly",
         "torso upright, arms relaxed at the sides"),
        "Plano sagital. Flexão plantar do tornozelo. Gastrocnêmio e sóleo (tríceps sural); concêntrica na subida, "
        "excêntrica na descida.",
        "Descer devagar, sem soltar o corpo de uma vez; se faltar equilíbrio, reduzir a altura."),
    Movement(
        "single_leg_balance", "Equilíbrio numa perna", "three-quarter front view",
        "standing tall with feet together, both hands on the hips, torso upright, eyes forward",
        "shifts the weight onto one leg and lifts the other knee until the thigh is about 45 degrees in front of the "
        "body, holding steady with the torso upright",
        "lowers the foot slowly back to the floor next to the other foot",
        ("stand tall, hips level", "eyes fixed on one point ahead", "hands stay on the hips, no grabbing for balance",
         "supporting knee soft, not locked"),
        "Plano sagital e controle postural. Flexão de quadril da perna livre; glúteo médio, quadríceps e tornozelo da perna "
        "de apoio estabilizam.",
        "Elevação pequena do joelho, só o tempo que o equilíbrio permitir; trocar de perna."),
    Movement(
        "biceps_curl", "Rosca de bíceps", "three-quarter front view",
        "standing tall with feet hip-width apart, holding a light dumbbell or water bottle in each hand, arms hanging "
        "straight with the palms facing forward, elbows close to the ribs, shoulders relaxed",
        "bends both elbows and curls the weights up toward the shoulders, the upper arms staying still against the body and "
        "the wrists straight, until the forearms are almost vertical",
        "lowers the weights slowly until the arms are straight again",
        ("elbows stay pinned to the sides", "no swinging or leaning back", "wrists straight", "shoulders relaxed, not shrugged"),
        "Plano sagital. Flexão de cotovelo. Bíceps braquial e braquial; concêntrica na subida, excêntrica na descida.",
        "Carga leve (garrafa de água); sem balançar o corpo."),
    Movement(
        "lateral_raise", "Elevação lateral dos braços", "front view",
        "standing tall facing the camera with feet hip-width apart, a light weight in each hand at the sides, palms facing "
        "the thighs, elbows very slightly bent, shoulders relaxed",
        "lifts both arms out to the sides, leading with the elbows, until the hands reach shoulder height with the arms "
        "slightly bent and the palms facing down",
        "lowers both arms slowly back to the sides",
        ("arms rise no higher than shoulder height", "no shrugging of the shoulders", "no swinging of the trunk",
         "elbows lead, slight bend kept"),
        "Plano frontal (ou da escápula). Abdução do ombro até 90 graus. Deltoide médio e supraespinhal; trapézio estabiliza.",
        "Carga leve; nunca acima do ombro; sem balançar."),
    Movement(
        "front_raise", "Elevação frontal dos braços", "three-quarter front view",
        "standing tall with feet hip-width apart, a light weight in each hand resting in front of the thighs, palms facing "
        "the body, elbows slightly bent, shoulders relaxed",
        "raises both arms straight forward to shoulder height, the palms facing down, the torso staying upright",
        "lowers both arms slowly back in front of the thighs",
        ("arms rise to shoulder height, no higher", "torso upright, no leaning back", "no shrugging",
         "slow and controlled"),
        "Plano sagital. Flexão do ombro até 90 graus. Deltoide anterior e porção clavicular do peitoral maior.",
        "Carga leve; sem arquear a lombar."),
    Movement(
        "overhead_press", "Desenvolvimento acima da cabeça", "front view",
        "standing tall facing the camera with feet hip-width apart, a light weight in each hand held at shoulder height "
        "with the elbows bent in front of the body and the forearms vertical, palms facing forward, ribs down",
        "presses both weights straight up until the arms are fully extended overhead beside the ears, without arching the "
        "lower back and without moving the legs",
        "lowers the weights slowly back to shoulder height",
        ("legs stay straight, no leg drive", "ribs down, no arching of the lower back", "forearms vertical, wrists straight",
         "arms finish fully extended over the shoulders"),
        "Plano frontal/escapular. Flexão e abdução do ombro com rotação superior da escápula e extensão do cotovelo. "
        "Deltoides, tríceps braquial e trapézio.",
        "Carga leve; sem arquear a lombar. Parar se houver dor no ombro."),
    Movement(
        "bent_over_row", "Remada com o tronco inclinado", "side view",
        "standing with feet hip-width apart, knees softly bent, the torso tipped forward about 45 degrees from the hips with "
        "a flat straight back, a light weight in each hand hanging straight down below the shoulders",
        "pulls both elbows back and up toward the hips, squeezing the shoulder blades together, bringing the weights to the "
        "sides of the ribs while the torso stays still",
        "lowers the weights slowly until the arms are straight again",
        ("spine neutral, no rounding", "torso angle stays fixed", "elbows travel close to the body",
         "squeeze the shoulder blades together"),
        "Plano sagital. Extensão do ombro e retração da escápula com flexão do cotovelo. Grande dorsal, romboides, "
        "trapézio médio e bíceps; eretores seguram a posição.",
        "Carga leve; inclinação de até 45 graus; coluna sempre reta."),
    Movement(
        "side_bend", "Alongamento lateral", "front view",
        "standing tall facing the camera with feet hip-width apart, one arm hanging at the side and the other arm "
        "relaxed, chest open, hips level",
        "raises one arm overhead and bends the torso sideways toward the opposite side in the frontal plane, the hips "
        "staying level, without twisting or leaning forward, until a gentle stretch is felt along the side",
        "slowly lifts the torso back to upright and lowers the arm to the side",
        ("hips stay level and square", "no twisting or leaning forward", "gentle stretch, no bouncing", "breathe slowly"),
        "Plano frontal. Flexão lateral do tronco. Oblíquos, quadrado lombar e dorsal; alonga o lado oposto.",
        "Amplitude confortável, sem forçar nem balançar."),
    Movement(
        "trunk_rotation", "Rotação de tronco em pé", "front view",
        "standing tall facing the camera with feet hip-width apart, knees soft, arms crossed over the chest, hips square",
        "rotates the upper body slowly to one side while the hips and feet stay facing forward, the head following the "
        "rotation, until about 45 degrees of turn",
        "rotates back through the center to the other side with the same small range, then returns to the start",
        ("hips and feet stay facing forward", "rotation comes from the upper back, small range",
         "stay tall, no leaning", "slow and smooth"),
        "Plano transversal. Rotação do tronco. Oblíquos interno e externo, multífidos e rotadores.",
        "Amplitude pequena (até cerca de 45 graus), sem girar os pés nem a lombar."),
    Movement(
        "arm_circles", "Círculos pequenos com os braços", "front view",
        "standing tall facing the camera with feet hip-width apart, both arms extended out to the sides at shoulder height, "
        "palms facing down, shoulders relaxed",
        "draws small slow circles forward with both arms at once, the arms staying at shoulder height",
        "reverses and draws small slow circles backward with both arms",
        ("circles stay small and slow", "arms stay at shoulder height", "shoulders relaxed, no shrugging",
         "torso still and upright"),
        "Plano frontal com rotação do ombro (circundução). Deltoides e manguito rotador (aquecimento de mobilidade).",
        "Círculos pequenos e lentos; sem carga; parar se houver dor no ombro."),
    Movement(
        "chest_opener", "Abertura de peito", "front view",
        "standing tall facing the camera with feet hip-width apart, both arms held out in front at chest height with the "
        "elbows slightly bent, palms facing each other, shoulders relaxed",
        "opens both arms wide to the sides in the horizontal plane, squeezing the shoulder blades together and lifting the "
        "chest slightly, until the hands are in line with the shoulders",
        "brings the arms slowly back together in front of the chest",
        ("shoulder blades squeeze together", "chest lifts, ribs stay down", "arms stay at chest height",
         "no shrugging"),
        "Plano transversal. Abdução horizontal do ombro e retração da escápula. Deltoide posterior, romboides e trapézio "
        "médio; alonga o peitoral.",
        "Sem carga; amplitude confortável; sem arquear a lombar."),
    Movement(
        "standing_march", "Marcha no lugar", "side view",
        "standing tall with feet hip-width apart, arms bent at the sides at about 90 degrees, torso upright",
        "lifts one knee until the thigh is about hip height while the opposite arm swings forward, keeping the torso upright",
        "lowers that foot to the floor and lifts the other knee with the opposite arm swinging forward",
        ("torso upright", "knees lift to hip height at most", "opposite arm and leg move together", "land softly"),
        "Plano sagital. Flexão de quadril alternada com balanço dos braços. Iliopsoas, quadríceps e glúteos de apoio.",
        "Pausado até 2026-10-20 por decisão do canal."),
)}


def get(movement_id: str) -> Movement:
    if movement_id not in MOVEMENTS:
        raise ValueError(f"Movimento desconhecido: {movement_id!r}. Use um de: {', '.join(MOVEMENTS)}.")
    return MOVEMENTS[movement_id]


def catalog() -> list[dict[str, Any]]:
    """Para o roteirista: o que cada movimento é, como se executa e os cuidados (em português, com os pontos em inglês)."""
    return [{"id": m.id, "label": m.label, "cues": list(m.cues), "kinesiology": m.kinesiology, "safety": m.safety}
            for m in MOVEMENTS.values()]


def keyframe_text(movement: Movement) -> str:
    """Posição inicial para o prompt da imagem-chave (com peso, para não se perder atrás do cenário)."""
    return f"({movement.view}, full body visible head to feet, one single person, {movement.setup}:1.3)"


def motion_texts(movement: Movement, clips: int) -> list[str]:
    """Um prompt por clipe do Wan: primeira fase, volta, primeira fase de novo... com ritmo e pontos de execução."""
    cues = "; ".join(movement.cues)
    texts = []
    for index in range(max(clips, 1)):
        phase = movement.go if index % 2 == 0 else movement.back
        again = " again" if index >= 2 else ""
        texts.append(f"The person{again} {phase}. {TEMPO}. Execution points: {cues}. {CAMERA}")
    return texts


def avoid(movement: Movement) -> str:
    """Negativo da imagem-chave (erros vistos nos testes: segunda pessoa, barriga de fora, sentada em exercício em pé)."""
    base = ("(hand on a wall:1.5), (leaning on a wall:1.5), (holding a railing:1.5), (holding onto furniture:1.4), "
            "(second person:1.4), (small figure in the background:1.4), (abs:1.5), (six-pack:1.5), (crop top:1.5), "
            "(sports bra:1.5), (bare midriff:1.5), (muscular arms:1.4), (barefoot:1.5), (distorted limbs:1.3), "
            "(extra arms:1.4), (extra legs:1.4)")
    return f"(sitting:1.5), (seated:1.5), (sitting on a chair:1.6), {base}" if movement.standing else base


def apply(scene: dict[str, Any]) -> dict[str, Any]:
    """Cena com `movement`: posição inicial no prompt da imagem e fases do movimento nos `motions` (idempotente)."""
    movement_id = scene.get("movement")
    if not movement_id or scene.get("showCharacter", True) is False:
        return scene
    movement = get(movement_id)
    text = keyframe_text(movement)
    prompt = scene["keyframePrompt"]
    if text not in prompt:
        prompt = f"{prompt.rstrip().rstrip('.')}, {text}"
    return {**scene, "keyframePrompt": prompt, "motions": motion_texts(movement, len(scene.get("motions") or [None, None]))}
