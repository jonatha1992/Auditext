from dataclasses import dataclass
from datetime import datetime

@dataclass
class TranscriptionRecord:
    file_path: str
    file_name: str
    duration: str
    transcription: str
    summary: str = ""
    language: str = ""
    created_at: str | None = None
    record_id: int | None = None
