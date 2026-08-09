"""Standalone AI-led oral practice view."""

from __future__ import annotations

from .interview_frame import InterviewFrame


class PracticeFrame(InterviewFrame):
    """Academic practice configured independently from question solving."""

    def __init__(self, parent):
        super().__init__(parent, fixed_mode="practica_oral")
