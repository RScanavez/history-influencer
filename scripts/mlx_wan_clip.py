"""Gera um clipe com o Wan 2.2 TI2V-5B no MLX (mlx-video), a partir de uma imagem inicial.

Roda no ambiente do MLX (MLX_PYTHON), não no .venv do projeto. Com --dmd usa o agendamento de 3 passos do FastWan
(sigmas do config.json do modelo, previsão de x0 e novo ruído a cada passo), que o mlx-video não traz.

Uso: <python-do-mlx> scripts/mlx_wan_clip.py --model-dir DIR --image IMG --prompt TXT --negative TXT
     --width 480 --height 832 --frames 61 --steps 20 --seed 7 --output clip.mp4 [--dmd]
"""
import argparse
import json
from pathlib import Path

import mlx.core as mx
from mlx_video.models.wan_2 import generate as gen
from mlx_video.models.wan_2 import scheduler as sch


def dmd_scheduler(sigmas: list[float]):
    class DMDScheduler:
        def __init__(self, num_train_timesteps: int = 1000):
            self.n = num_train_timesteps

        def set_timesteps(self, num_steps, shift=1.0):
            self.timesteps = mx.array([float(int(sigma * self.n)) for sigma in sigmas[:-1]])
            self._i = 0

        def step(self, model_output, timestep, sample):
            current, following = sigmas[self._i], sigmas[self._i + 1]
            x0 = sample - current * model_output  # fluxo: x_t = (1-s)·x0 + s·ruído e v = ruído - x0
            self._i += 1
            if following <= 0:
                return x0
            return (1 - following) * x0 + following * mx.random.normal(sample.shape)

        def reset(self):
            self._i = 0

    return DMDScheduler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--negative", default=None)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=832)
    parser.add_argument("--frames", type=int, default=61)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--guide", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dmd", action="store_true")
    args = parser.parse_args()
    steps, guide, solver = args.steps, args.guide, "unipc"
    if args.dmd:
        sigmas = json.loads((Path(args.model_dir) / "config.json").read_text())["fastwan_dmd"]["sigmas"]
        sch.FlowMatchEulerScheduler = dmd_scheduler(sigmas)  # o generate importa o agendador na hora da chamada
        steps, guide, solver = len(sigmas) - 1, 1.0, "euler"
    gen.generate_video(model_dir=args.model_dir, prompt=args.prompt, negative_prompt=args.negative, image=args.image,
                       width=args.width, height=args.height, num_frames=args.frames, steps=steps, guide_scale=guide,
                       seed=args.seed, output_path=args.output, scheduler=solver)


if __name__ == "__main__":
    main()
