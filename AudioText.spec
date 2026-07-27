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
FFMPEG_BIN = os.path.join(".", "ffmpeg", "bin")

# --- Collect packages that bundle data files ---
ctk_datas, ctk_bins, ctk_hiddens = collect_all("customtkinter")
fw_datas = collect_data_files("faster_whisper")
sc_datas = collect_data_files("soundcard")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[
        (os.path.join(FFMPEG_BIN, "ffmpeg.exe"), "ffmpeg/bin"),
        (os.path.join(FFMPEG_BIN, "ffprobe.exe"), "ffmpeg/bin"),
    ] + ctk_bins,
    datas=[
        ("icons/icono.ico", "icons"),
        (WHISPER_SMALL, "models/whisper-small"),
    ] + ctk_datas + fw_datas + sc_datas,
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
    ] + ctk_hiddens,
    hookspath=["hooks"],
    hooksconfig={},
    runtime_hooks=["hooks/rthook_frozen.py"],
    excludes=[
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "cv2",
        "tkinter.test",
        "xmlrpc",
        "ftplib",
        "imaplib",
        "smtplib",
        "torch",
        "torchaudio",
        "torchvision",
        "speechbrain",
        "whisperx",
        "pyannote",
        "simple_diarizer",
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
    upx=False,
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
    upx=False,
    name="AudioText",
)
