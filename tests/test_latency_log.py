import unittest
from unittest.mock import patch

from infrastructure.services import latency_log


class LogStageTests(unittest.TestCase):
    def _emitted(self, *args, **kwargs) -> str:
        """Return the fully formatted line log_stage would write."""
        with patch.object(latency_log.config, "logger") as logger:
            latency_log.log_stage(*args, **kwargs)
            self.assertTrue(logger.info.called, "log_stage no emitió nada")
            fmt, *params = logger.info.call_args[0]
            return fmt % tuple(params)

    def test_emits_prefixed_greppable_line(self):
        line = self._emitted("file", "transcribe_file", 1234.6)
        self.assertEqual(line, "LATENCY pipeline=file stage=transcribe_file ms=1235")

    def test_serializes_extra_fields_as_key_value(self):
        line = self._emitted("file", "transcribe_file", 100.0, rtf="0.42", segments=7)
        self.assertIn("rtf=0.42", line)
        self.assertIn("segments=7", line)

    def test_omits_none_fields(self):
        # `aborted` and `provider` are passed as None on the happy path; a
        # literal "aborted=None" in the log would read as a real abort.
        line = self._emitted("file", "transcribe_file", 100.0, aborted=None, chars=5)
        self.assertNotIn("aborted", line)
        self.assertIn("chars=5", line)

    def test_never_raises_when_logging_fails(self):
        with patch.object(latency_log.config, "logger") as logger:
            logger.info.side_effect = RuntimeError("logger caído")
            latency_log.log_stage("file", "transcribe_file", 1.0)  # must not raise


class TimedTests(unittest.TestCase):
    def test_logs_fields_added_inside_the_block(self):
        with patch.object(latency_log, "log_stage") as log_stage:
            with latency_log.timed("model", "load", size="small") as info:
                info["segments"] = 3

        pipeline, stage, _ms = log_stage.call_args[0]
        self.assertEqual((pipeline, stage), ("model", "load"))
        self.assertEqual(log_stage.call_args[1], {"size": "small", "segments": 3})

    def test_logs_even_when_the_block_raises(self):
        with patch.object(latency_log, "log_stage") as log_stage:
            with self.assertRaises(ValueError):
                with latency_log.timed("model", "load"):
                    raise ValueError("boom")
        self.assertTrue(log_stage.called)


class RtfTests(unittest.TestCase):
    def test_ratio_of_elapsed_to_audio(self):
        self.assertEqual(latency_log.rtf(500.0, 1000.0), "0.50")

    def test_none_when_audio_duration_is_unusable(self):
        # A silent or empty chunk would otherwise divide by zero.
        self.assertIsNone(latency_log.rtf(500.0, 0.0))
        self.assertIsNone(latency_log.rtf(500.0, -1.0))


if __name__ == "__main__":
    unittest.main()
