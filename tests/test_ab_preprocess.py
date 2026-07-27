"""A/B test: transcribe audio WITH and WITHOUT preprocessing.

Compares results side by side for quality evaluation.
"""
import sys
import os
import time

# Must run from app_escritorio/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from infrastructure.audio import audio_preprocessor
from faster_whisper import WhisperModel

MODEL_SIZE = "small"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"

# Test files — pick a variety
TEST_FILES = [
    r"d:\Repositorio\Auditext\Audios\LP41335178.wav",         # Short telephony
    r"d:\Repositorio\Auditext\Audios\PRUEBA (1).aac",         # AAC format
    r"d:\Repositorio\Auditext\Audios\WhatsApp Ptt 2024-08-07 at 10.13.42.ogg",  # WhatsApp voice note
]

def transcribe_raw(model, path):
    """Transcribe without any preprocessing."""
    segments, info = model.transcribe(
        path, vad_filter=True, condition_on_previous_text=False
    )
    parts = [seg.text.strip() for seg in segments if seg.text.strip()]
    return " ".join(parts), info.language

def transcribe_preprocessed(model, path):
    """Transcribe with full preprocessing pipeline."""
    preprocessed, is_temp = audio_preprocessor.preprocess(path)
    try:
        segments, info = model.transcribe(
            preprocessed, vad_filter=True, condition_on_previous_text=False
        )
        parts = [seg.text.strip() for seg in segments if seg.text.strip()]
        return " ".join(parts), info.language
    finally:
        if is_temp and os.path.exists(preprocessed):
            os.remove(preprocessed)

def main():
    print("=" * 70)
    print("AUDIO PREPROCESSING A/B TEST")
    print("=" * 70)
    print(f"Model: {MODEL_SIZE} | Device: {DEVICE} | Compute: {COMPUTE_TYPE}")
    print()

    print("Loading model...")
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print("Model loaded.\n")

    for path in TEST_FILES:
        if not os.path.exists(path):
            print(f"SKIP: {path} (not found)")
            continue

        fname = os.path.basename(path)
        size_kb = os.path.getsize(path) // 1024
        print("-" * 70)
        print(f"FILE: {fname} ({size_kb} KB)")
        print("-" * 70)

        # A: Raw
        t0 = time.time()
        text_raw, lang_raw = transcribe_raw(model, path)
        time_raw = time.time() - t0

        # B: Preprocessed
        t0 = time.time()
        text_prep, lang_prep = transcribe_preprocessed(model, path)
        time_prep = time.time() - t0

        print(f"\n  [RAW]          lang={lang_raw}  time={time_raw:.1f}s")
        print(f"  Words: {len(text_raw.split())}")
        print(f"  >>> {text_raw[:300]}")

        print(f"\n  [PREPROCESSED] lang={lang_prep}  time={time_prep:.1f}s")
        print(f"  Words: {len(text_prep.split())}")
        print(f"  >>> {text_prep[:300]}")

        # Compare
        if text_raw.strip() == text_prep.strip():
            print("\n  RESULT: Identical output")
        else:
            raw_words = set(text_raw.lower().split())
            prep_words = set(text_prep.lower().split())
            common = len(raw_words & prep_words)
            total = len(raw_words | prep_words)
            similarity = (common / total * 100) if total else 100
            print(f"\n  RESULT: Different — {similarity:.0f}% word overlap")
            # Show differences
            only_raw = raw_words - prep_words
            only_prep = prep_words - raw_words
            if only_raw:
                print(f"  Only in RAW:  {' '.join(list(only_raw)[:10])}")
            if only_prep:
                print(f"  Only in PREP: {' '.join(list(only_prep)[:10])}")

        print()

    print("=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
