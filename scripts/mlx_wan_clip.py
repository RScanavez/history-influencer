"""Gera um clipe com o Wan 2.2 TI2V-5B no MLX (mlx-video), a partir de uma imagem inicial.

Roda no ambiente do MLX (MLX_PYTHON), não no .venv do projeto. Com --dmd usa o agendamento de 3 passos do FastWan
(sigmas do config.json do modelo, previsão de x0 e novo ruído a cada passo), que o mlx-video não traz.

Com --taehv troca o VAE completo do Wan 2.2 (o gargalo: 100-300 s por clipe) pelo decodificador minúsculo TAEHV em MLX.
Com --rife gera só metade dos quadros e preenche o resto com o RIFE 4.25 em MLX (modo rápido do FastMetal). Os dois
vêm do FastVideo, copiados em <ferramentas>/mlx/vendor (--vendor).

Uso: <python-do-mlx> scripts/mlx_wan_clip.py --model-dir DIR --image IMG --prompt TXT --negative TXT
     --width 480 --height 832 --frames 61 --steps 20 --seed 7 --output clip.mp4 [--dmd]
     [--vendor DIR --taehv taew2_2.pth --rife DIR-DO-RIFE]
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx_video.models.wan_2 import generate as gen
from mlx_video.models.wan_2 import scheduler as sch
from mlx_video.models.wan_2 import vae22


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


def use_taehv(checkpoint: str) -> None:
    """O generate passa a decodificar com o TAEHV: ele recebe os latentes crus, sem desnormalizar."""
    from taehv_mlx import MLXTAEHVDecoder

    class TAEHVVae:
        def __init__(self):
            self.decoder = MLXTAEHVDecoder(Path(checkpoint), z_dim=48)

        def __call__(self, z):  # z: [1, T, H, W, C] -> [1, T', H', W', 3] em [-1, 1], como o VAE do mlx-video
            started = time.time()
            video = self.decoder.decode_ntchw(z.transpose(0, 1, 4, 2, 3)).transpose(0, 1, 3, 4, 2) * 2.0 - 1.0
            mx.eval(video)
            print(f"  TAEHV decode: {time.time() - started:.1f}s", flush=True)
            return video

        def decode_tiled(self, z, _tiling):
            return self(z)

    gen.load_vae_decoder = lambda *_args, **_kwargs: TAEHVVae()
    vae22.denormalize_latents = lambda z: z


def use_rife(weights_dir: str, target_frames: int) -> None:
    """Na hora de salvar, preenche os quadros que faltam até target_frames com o RIFE."""
    from rife_mlx.pipeline_mlx import interpolate_pair
    from rife_mlx.utils.weights import build_model

    save_video = gen.save_video

    def save_interpolated(video, output_path, fps=24, **kwargs):
        started = time.time()
        model = build_model("4.25", weights_dir)
        frames = list(video)
        out = []
        for position in np.linspace(0.0, len(frames) - 1, target_frames):
            left = min(int(position), len(frames) - 2)
            fraction = float(position - left)
            if fraction < 1e-6:
                out.append(frames[left])
            elif fraction > 1 - 1e-6:
                out.append(frames[left + 1])
            else:
                out.append(interpolate_pair(model, frames[left], frames[left + 1], fraction))
        print(f"  RIFE {len(frames)} -> {len(out)} quadros: {time.time() - started:.1f}s", flush=True)
        save_video(np.stack(out), output_path, fps=fps, **kwargs)

    gen.save_video = save_interpolated


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
    parser.add_argument("--vendor", default=None)
    parser.add_argument("--taehv", default=None)
    parser.add_argument("--rife", default=None)
    args = parser.parse_args()
    if args.vendor:
        sys.path.insert(0, args.vendor)
    frames = args.frames
    if args.taehv:
        use_taehv(args.taehv)
    if args.rife:
        use_rife(args.rife, args.frames)
        frames = 4 * math.ceil((args.frames - 1) / 2 / 4) + 1  # metade, na grade 4k+1 do Wan
    steps, guide, solver = args.steps, args.guide, "unipc"
    if args.dmd:
        sigmas = json.loads((Path(args.model_dir) / "config.json").read_text())["fastwan_dmd"]["sigmas"]
        sch.FlowMatchEulerScheduler = dmd_scheduler(sigmas)  # o generate importa o agendador na hora da chamada
        steps, guide, solver = len(sigmas) - 1, 1.0, "euler"
    gen.generate_video(model_dir=args.model_dir, prompt=args.prompt, negative_prompt=args.negative, image=args.image,
                       width=args.width, height=args.height, num_frames=frames, steps=steps, guide_scale=guide,
                       seed=args.seed, output_path=args.output, scheduler=solver)


if __name__ == "__main__":
    main()
