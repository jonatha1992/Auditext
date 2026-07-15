from abc import ABC, abstractmethod
from typing import Callable, Any
from core.domain.entities import TranscriptionRecord

class TranscriptionRepository(ABC):
    @abstractmethod
    def init_db(self) -> None:
        """Initialize the database schema."""
        pass

    @abstractmethod
    def save(self, record: TranscriptionRecord) -> str:
        """Save or replace a transcription record. Returns the saved audio path."""
        pass

    @abstractmethod
    def get(self, file_path: str) -> TranscriptionRecord | None:
        """Retrieve a transcription record by its local file path."""
        pass

    @abstractmethod
    def get_all(self) -> list[TranscriptionRecord]:
        """Retrieve all transcription records ordered by creation date desc."""
        pass

    @abstractmethod
    def delete(self, file_path: str) -> None:
        """Delete a transcription record by its local file path."""
        pass

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """Return database statistics (e.g. database file size, total records)."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all records from the repository."""
        pass

    @abstractmethod
    def update_summary(self, file_path: str, summary: str) -> None:
        """Update the summary field of a transcription record."""
        pass


class TranscriptionService(ABC):
    @abstractmethod
    def transcribe_file(
        self,
        file_path: str,
        language: str | None,
        translate: bool,
        progress_cb: Callable[[float], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> str:
        """Transcribe an audio or video file offline and return the transcript text."""
        pass

    @abstractmethod
    def transcribe_file_segments(
        self,
        file_path: str,
        language: str | None,
        translate: bool,
        progress_cb: Callable[[float], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> list[tuple[float, float, str]]:
        """Transcribe an audio or video file offline and return segments with timestamps."""
        pass

    @abstractmethod
    def get_model(self) -> Any:
        """Retrieve or pre-load the underlying transcription model."""
        pass

    @abstractmethod
    def transcribe_array(
        self,
        audio: Any,
        language: str | None = None,
        translate: bool = False,
    ) -> tuple[list[str], str | None]:
        """Transcribe an in-memory audio array and return the segment texts and detected language."""
        pass

