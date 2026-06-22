"""Audio preprocessing pipeline for improved transcription accuracy.

Prepares audio before sending it to faster-whisper / whisperx by applying:

1. **Mono 16 kHz conversion** — matches Whisper's native format.
2. **EBU R128 loudness normalization** (-16 LUFS) — consistent volume.
3. **High-pass filter at 80 Hz** — removes low rumble / vibrations.
4. **Dynamic range compression** — evens out loud vs quiet speakers.
5. **Spectral noise reduction** (optional) — via ``noisereduce`` library.

All ffmpeg-based steps run in a single subprocess call (no intermediate
files). The optional noise reduction step requires loading the audio into
numpy, so it adds a second pass when enabled.

Usage::

    path, is_temp = preprocess("interview.mp3")
    # ... pass *path* to Whisper ...
    if is_temp:
        os.remove(path)
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile

import numpy as np

from config import ffmpeg_path, logger


def preprocess(
    input_path: str,
    *,
    normalize: bool = True,
    highpass: bool = True,
    compress_dynamic: bool = False,
    denoise: bool = False,
    denoise_strength: float = 0.4,
    progress_cb=None,
) -> tuple[str, bool]:
    """Run the full preprocessing pipeline.

    Args:
        input_path: Path to the source audio file.
        normalize: Apply EBU R128 loudness normalization (-16 LUFS).
        highpass: Apply 80 Hz high-pass filter to remove sub-bass rumble.
        compress_dynamic: Apply gentle dynamic range compression.
        denoise: Apply spectral noise reduction (adds ~1-2 s).
        denoise_strength: How aggressive the noise reduction is (0.0–1.0).
            Lower values preserve more of the original signal.
        progress_cb: Optional callable(fraction) for progress updates.

    Returns:
        (output_path, is_temp) — caller must delete output_path when
        is_temp is True.
    """
    if progress_cb:
        progress_cb(0.0)

    # Build the ffmpeg audio filter chain
    filters: list[str] = []

    if highpass:
        filters.append("highpass=f=80")

    if compress_dynamic:
        # Gentle compression: quiet sounds up, loud sounds slightly down.
        # attack=0.3s, release=1s, soft knee.
        filters.append(
            "acompressor=threshold=-24dB:ratio=3:attack=300:release=1000:knee=6dB:makeup=2dB"
        )

    if normalize:
        # EBU R128 loudness normalization — single-pass (linear mode).
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11:linear=true")

    # Always convert to mono 16 kHz — Whisper's native format.
    tmp_wav = tempfile.mktemp(suffix=".wav")

    cmd = [
        ffmpeg_path,
        "-y",
        "-i", input_path,
    ]

    if filters:
        cmd.extend(["-af", ",".join(filters)])

    cmd.extend([
        "-ar", "16000",
        "-ac", "1",
        "-sample_fmt", "s16",
        tmp_wav,
    ])

    startupinfo = None
    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        subprocess.run(
            cmd,
            startupinfo=startupinfo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "ffmpeg preprocessing failed for %s: %s", input_path, exc.stderr
        )
        # Fallback: return the original file unchanged.
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
        return input_path, False

    if progress_cb:
        progress_cb(0.6)

    # Optional: spectral noise reduction (numpy-based second pass)
    if denoise:
        tmp_wav = _apply_noise_reduction(tmp_wav, denoise_strength)

    if progress_cb:
        progress_cb(1.0)

    logger.info(
        "Audio preprocessed: %s → %s (normalize=%s, highpass=%s, compress=%s, denoise=%s)",
        input_path, tmp_wav, normalize, highpass, compress_dynamic, denoise,
    )
    return tmp_wav, True


def _apply_noise_reduction(wav_path: str, strength: float) -> str:
    """Apply spectral noise reduction using the noisereduce library.

    Modifies the WAV file in-place and returns the same path.
    Falls back silently if noisereduce is not installed.
    """
    try:
        import noisereduce as nr
        from scipy.io import wavfile
    except ImportError:
        logger.warning(
            "noisereduce or scipy not installed — skipping noise reduction. "
            "Install with: pip install noisereduce"
        )
        return wav_path

    try:
        rate, data = wavfile.read(wav_path)

        # Convert to float32 for noisereduce
        if data.dtype == np.int16:
            audio_float = data.astype(np.float32) / 32768.0
        elif data.dtype == np.float32:
            audio_float = data
        else:
            audio_float = data.astype(np.float32)

        # Stationary noise reduction — conservative by default.
        # prop_decrease controls how much of the noise is removed (0–1).
        reduced = nr.reduce_noise(
            y=audio_float,
            sr=rate,
            stationary=True,
            prop_decrease=strength,
            n_fft=2048,
            n_std_thresh_stationary=1.5,
        )

        # Convert back to int16 for WAV
        reduced_int16 = (reduced * 32768.0).clip(-32768, 32767).astype(np.int16)
        wavfile.write(wav_path, rate, reduced_int16)

        logger.info("Noise reduction applied (strength=%.2f)", strength)
    except Exception as exc:
        logger.warning("Noise reduction failed, using original audio: %s", exc)

    return wav_path


def estimate_noise_level(wav_path: str) -> float:
    """Estimate the noise level of a WAV file.

    Returns a value between 0.0 (clean) and 1.0 (very noisy).
    Uses the RMS of the quietest 10% of frames as a proxy for
    the noise floor.
    """
    try:
        from scipy.io import wavfile

        rate, data = wavfile.read(wav_path)
        if data.dtype == np.int16:
            audio = data.astype(np.float32) / 32768.0
        else:
            audio = data.astype(np.float32)

        # Split into 50ms frames
        frame_size = int(rate * 0.05)
        n_frames = len(audio) // frame_size
        if n_frames < 2:
            return 0.0

        frames = audio[: n_frames * frame_size].reshape(n_frames, frame_size)
        rms_per_frame = np.sqrt(np.mean(frames ** 2, axis=1))

        # Noise floor = mean RMS of the quietest 10% of frames
        sorted_rms = np.sort(rms_per_frame)
        bottom_10 = sorted_rms[: max(1, n_frames // 10)]
        noise_floor = float(np.mean(bottom_10))

        # Normalize: 0.01 RMS ≈ "noisy" threshold
        return min(noise_floor / 0.01, 1.0)

    except Exception as exc:
        logger.warning("Could not estimate noise level: %s", exc)
        return 0.0
