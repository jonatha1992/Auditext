"""Run one audio file through every STT engine and compare them.

    python tools/bench_transcription.py audio.wav
    python tools/bench_transcription.py audio.wav --repeat 3
    python tools/bench_transcription.py audio.wav --motores whisper gemini
    python tools/bench_transcription.py audio.wav --referencia transcripcion.txt

Reports wall time, RTF (real-time factor: <1.00 means faster than playback) and
availability per engine. Quality is only scored when --referencia points at a
hand-written transcript; without one the script prints the texts side by side
and refuses to invent an accuracy number.

Whisper's model is loaded before the clock starts, otherwise the one-off model
load would be charged to Whisper and to no one else.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config  # noqa: E402
from infrastructure.services import gemini_transcriber, stt_file_adapters  # noqa: E402
from infrastructure.services.onnx_transcriber import (  # noqa: E402
    OfflineTranscriptionService,
)


def audio_duration_seconds(path: Path) -> float:
    """Duration of a PCM WAV. Returns 0.0 for formats wave cannot open."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            return wf.getnframes() / float(rate) if rate else 0.0
    except (wave.Error, EOFError, OSError):
        return 0.0


def normalize(text: str) -> list[str]:
    """Lowercase word list, punctuation stripped — the basis for WER."""
    keep = [c.lower() if c.isalnum() or c.isspace() else " " for c in text]
    return "".join(keep).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by reference length."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return float("nan")

    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(
                previous[j - 1] if r == h
                else 1 + min(previous[j - 1], previous[j], current[j - 1])
            )
        previous = current
    return previous[-1] / len(ref)


_whisper_singleton: OfflineTranscriptionService | None = None


def whisper_service() -> OfflineTranscriptionService:
    """One service for warm-up and for every run.

    Two instances would mean two lazy model loads, and the load would land
    inside the measured window — exactly what the warm-up exists to avoid.
    """
    global _whisper_singleton
    if config.transcription_service is not None:
        return config.transcription_service
    if _whisper_singleton is None:
        _whisper_singleton = OfflineTranscriptionService()
    return _whisper_singleton


def run_whisper(path: Path, language: str | None) -> tuple[str, dict]:
    text = whisper_service().transcribe_file(
        str(path), language=language, translate=False
    )
    return text, {}


def run_gemini(path: Path, language: str | None) -> tuple[str, dict]:
    return gemini_transcriber.transcribe_file(path, language=language)


def run_google(path: Path, language: str | None) -> tuple[str, dict]:
    return stt_file_adapters.transcribe_google(path, language=language)


def run_windows(path: Path, language: str | None) -> tuple[str, dict]:
    return stt_file_adapters.transcribe_windows(path, language=language)


ENGINES = {
    "whisper": ("Whisper small (local)", run_whisper),
    "gemini": ("Gemini (nube)", run_gemini),
    "google": ("Google STT (nube)", run_google),
    "windows": ("Windows STT (local)", run_windows),
}


def warm_up(engines: list[str]) -> None:
    """Load the Whisper model outside the measured window."""
    if "whisper" not in engines:
        return
    print("Precargando el modelo Whisper (fuera de la medición)...")
    whisper_service().get_model()


def bench_engine(
    name: str, path: Path, language: str | None, repeat: int
) -> dict:
    label, runner = ENGINES[name]
    times: list[float] = []
    text = ""
    meta: dict = {}

    for attempt in range(1, repeat + 1):
        start = time.perf_counter()
        try:
            text, meta = runner(path, language)
        except Exception as exc:
            return {"label": label, "error": str(exc)}
        times.append((time.perf_counter() - start) * 1000.0)
        print(f"  {label}: corrida {attempt}/{repeat} — {times[-1]:.0f} ms")

    return {
        "label": label,
        "median_ms": statistics.median(times),
        "times": times,
        "text": text,
        "meta": meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Archivo de audio (WAV PCM recomendado)")
    parser.add_argument(
        "--motores",
        nargs="+",
        choices=sorted(ENGINES),
        default=sorted(ENGINES),
        help="Motores a comparar (por defecto, todos)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Corridas por motor; se reporta la mediana")
    parser.add_argument("--idioma", default=None, help="Código de idioma, p. ej. es o en")
    parser.add_argument(
        "--referencia",
        type=Path,
        default=None,
        help="Transcripción correcta hecha a mano; habilita el cálculo de WER",
    )
    parser.add_argument("--texto", action="store_true", help="Imprimir el texto completo de cada motor")
    args = parser.parse_args()

    if not args.audio.is_file():
        print(f"No existe el archivo: {args.audio}", file=sys.stderr)
        return 1

    duration = audio_duration_seconds(args.audio)
    reference = args.referencia.read_text(encoding="utf-8") if args.referencia else None

    print(f"\nAudio: {args.audio.name}")
    print(f"Duración: {duration:.1f} s" if duration else "Duración: desconocida (no es WAV PCM)")
    print(f"Motores: {', '.join(args.motores)} · corridas: {args.repeat}\n")

    warm_up(args.motores)

    results = {}
    for name in args.motores:
        print(f"Midiendo {ENGINES[name][0]}...")
        results[name] = bench_engine(name, args.audio, args.idioma, args.repeat)

    header = f"\n{'Motor':<24}{'Mediana':>12}{'RTF':>8}{'Chars':>8}"
    if reference:
        header += f"{'WER':>8}"
    print(header)
    print("-" * len(header.strip("\n")))

    for name in args.motores:
        r = results[name]
        if "error" in r:
            print(f"{r['label']:<24}{'no disponible':>12}   {r['error'][:40]}")
            continue
        rtf = r["median_ms"] / (duration * 1000.0) if duration else float("nan")
        row = (
            f"{r['label']:<24}{r['median_ms']:>10.0f}ms"
            f"{rtf:>8.2f}{len(r['text']):>8}"
        )
        if reference:
            row += f"{word_error_rate(reference, r['text']):>8.2f}"
        print(row)

    google = results.get("google")
    if google and "meta" in google and google["meta"].get("chunks"):
        meta = google["meta"]
        print(
            f"\nNota: Google STT procesó {meta['chunks']} tramos de "
            f"{meta['chunk_seconds']} s ({meta['failed_chunks']} fallaron). "
            "Un tiempo bajo puede significar que transcribió menos."
        )

    if not reference:
        print(
            "\nSin --referencia no hay puntaje de calidad. Compará los textos "
            "con --texto y juzgá a ojo."
        )

    if args.texto:
        for name in args.motores:
            r = results[name]
            print(f"\n{'=' * 70}\n{r['label']}\n{'=' * 70}")
            print(r.get("error") or r["text"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
