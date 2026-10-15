"""Sintetiza narração com a Kokoro 82M (Apache 2.0); padrão português do Brasil, --lang f para francês, --lang a para inglês americano.

Roda no ambiente próprio da Kokoro (KOKORO_PYTHON). Lê o texto da entrada padrão em UTF-8 e grava um WAV
mono de 24 kHz, com a mesma interface de linha de comando usada para o Piper.

Uso: <python-da-kokoro> scripts/kokoro_tts.py --voice pm_santa --speed 1.0 --output_file cena.wav < texto
"""
import argparse
import sys

import numpy as np
import soundfile as sf
from kokoro import KPipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="pm_santa")
    parser.add_argument("--speed", type=float, default=1.0)
    # Código de idioma da Kokoro: p = português do Brasil, f = francês, a = inglês americano.
    parser.add_argument("--lang", default="p")
    parser.add_argument("--output_file", required=True)
    args = parser.parse_args()
    # utf-8-sig descarta o BOM que o PowerShell às vezes acrescenta ao texto enviado por pipe.
    text = sys.stdin.buffer.read().decode("utf-8-sig").strip()
    if not text:
        raise SystemExit("Texto vazio para narrar.")
    pipeline = KPipeline(lang_code=args.lang, repo_id="hexgrad/Kokoro-82M")
    chunks = [result.audio.numpy() for result in pipeline(text, voice=args.voice, speed=args.speed)]
    if not chunks:
        raise SystemExit("A Kokoro não gerou áudio para o texto.")
    sf.write(args.output_file, np.concatenate(chunks), 24000)


if __name__ == "__main__":
    main()
