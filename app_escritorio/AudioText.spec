# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# --- Model paths (absolute) ---
HF_HUB = os.path.join(os.environ["USERPROFILE"], ".cache", "huggingface", "hub")
WHISPER_SMALL = os.path.join(
    HF_HUB,
    "models--Systran--faster-whisper-small",
    "snapshots",
    "536b0662742c02347bc0e980a01041f333bce120",
)
TORCH_HUB = os.path.join(os.environ["USERPROFILE"], ".cache", "torch", "hub")
SILERO_VAD = os.path.join(TORCH_HUB, "snakers4_silero-vad_master")
FFMPEG_BIN = os.path.join("..", "ffmpeg", "bin")

# --- Collect packages that bundle data files ---
ctk_datas, ctk_bins, ctk_hiddens = collect_all("customtkinter")
sb_datas, sb_bins, sb_hiddens = collect_all("speechbrain")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        (os.path.join(FFMPEG_BIN, "ffmpeg.exe"), "ffmpeg/bin"),
        (os.path.join(FFMPEG_BIN, "ffprobe.exe"), "ffmpeg/bin"),
    ] + ctk_bins + sb_bins,
    datas=[
        ("icons/icono.ico", "icons"),
        ("pretrained_models", "pretrained_models"),
        (WHISPER_SMALL, "models/whisper-small"),
        (SILERO_VAD, "torch_hub/snakers4_silero-vad_master"),
    ] + ctk_datas + sb_datas,
    hiddenimports=[
        # Audio capture
        "soundcard",
        "pycaw",
        "pycaw.pycaw",
        "comtypes",
        "comtypes.client",
        "comtypes.server",
        "comtypes.gen",
        # Transcription
        "ctranslate2",
        "faster_whisper",
        "faster_whisper.audio",
        "faster_whisper.transcribe",
        "faster_whisper.vad",
        "faster_whisper.tokenizer",
        "av",
        # Signal processing
        "scipy",
        "scipy.signal",
        "scipy.ndimage",
        "noisereduce",
        # Diarization
        "simple_diarizer",
        "simple_diarizer.diarizer",
        "simple_diarizer.cluster",
        "simple_diarizer.utils",
        "torchaudio",
        "torchaudio.backend",
        "sklearn",
        "sklearn.cluster",
        "sklearn.cluster._agglomerative",
        "sklearn.utils._cython_blas",
        "sklearn.neighbors._partition_nodes",
        # Media
        "mutagen",
        "mutagen.mp3",
        "mutagen.mp4",
        "mutagen.flac",
        "mutagen.ogg",
        "pygame",
        "pygame.mixer",
        # Misc
        "psutil",
        "docx",
        "google.genai",
        "dotenv",
        "speech_recognition",
        "pyaudio",
    ] + ctk_hiddens + sb_hiddens,
    hookspath=["hooks"],
    hooksconfig={},
    runtime_hooks=["hooks/rthook_frozen.py"],
    excludes=[
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "pandas",
        "cv2",
        "tkinter.test",
        "unittest",
        "xmlrpc",
        "ftplib",
        "imaplib",
        "smtplib",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioText",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "msvcp140.dll", "python3*.dll"],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icons\\icono.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "msvcp140.dll", "python3*.dll"],
    name="AudioText",
)
