import os
import unittest
import tempfile
import shutil
from core.domain.entities import TranscriptionRecord
from infrastructure.repositories.sqlite_repository import SQLiteTranscriptionRepository

class TestSQLiteTranscriptionRepository(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for the database and managed audio files
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_auditext.db")
        
        # We can use a dummy logger to suppress log clutter during testing
        class DummyLogger:
            def info(self, msg, *args): pass
            def error(self, msg, *args): pass
            def warning(self, msg, *args): pass
            
        self.repository = SQLiteTranscriptionRepository(self.db_path, logger_instance=DummyLogger())
        self.repository.init_db()

    def tearDown(self):
        # Clean up the temporary database and directory
        shutil.rmtree(self.test_dir)

    def test_init_db_creates_table(self):
        # The database file should exist
        self.assertTrue(os.path.exists(self.db_path))

    def test_save_and_get_all(self):
        record = TranscriptionRecord(
            file_path="dummy_path.wav",
            file_name="dummy_path.wav",
            duration="01:23",
            transcription="Hello world",
            summary="A test greeting",
            language="en"
        )
        saved_path = self.repository.save(record)
        self.assertEqual(saved_path, "dummy_path.wav")

        records = self.repository.get_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].file_name, "dummy_path.wav")
        self.assertEqual(records[0].transcription, "Hello world")
        self.assertEqual(records[0].summary, "A test greeting")
        self.assertEqual(records[0].language, "en")

    def test_get_by_path_exact_and_suffix(self):
        record = TranscriptionRecord(
            file_path="C:/path/to/123456_test.wav",
            file_name="test.wav",
            duration="01:00",
            transcription="Hello exact and suffix"
        )
        self.repository.save(record)
        
        # Test exact match
        r1 = self.repository.get("C:/path/to/123456_test.wav")
        self.assertIsNotNone(r1)
        self.assertEqual(r1.transcription, "Hello exact and suffix")

        # Test suffix match
        r2 = self.repository.get("D:/another/folder/test.wav")
        self.assertIsNotNone(r2)
        self.assertEqual(r2.transcription, "Hello exact and suffix")

    def test_duplicate_file_path_replaces_record(self):
        record1 = TranscriptionRecord(
            file_path="same_path.wav",
            file_name="first.wav",
            duration="01:00",
            transcription="First content"
        )
        record2 = TranscriptionRecord(
            file_path="same_path.wav",
            file_name="second.wav",
            duration="02:00",
            transcription="Second content"
        )
        self.repository.save(record1)
        self.repository.save(record2)

        records = self.repository.get_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].file_name, "second.wav")
        self.assertEqual(records[0].transcription, "Second content")

    def test_delete_record(self):
        record = TranscriptionRecord(
            file_path="delete_me.wav",
            file_name="delete_me.wav",
            duration="00:30",
            transcription="To be deleted"
        )
        self.repository.save(record)
        self.assertEqual(len(self.repository.get_all()), 1)

        self.repository.delete("delete_me.wav")
        self.assertEqual(len(self.repository.get_all()), 0)

    def test_rename_record(self):
        record = TranscriptionRecord(
            file_path="rename_me.wav",
            file_name="old_name.wav",
            duration="00:45",
            transcription="Content"
        )
        self.repository.save(record)
        
        success = self.repository.rename("rename_me.wav", "new_name.wav")
        self.assertTrue(success)
        
        records = self.repository.get_all()
        self.assertEqual(records[0].file_name, "new_name.wav")

    def test_get_stats(self):
        stats_empty = self.repository.get_stats()
        self.assertEqual(stats_empty["total_records"], 0)
        self.assertGreater(stats_empty["size_kb"], 0.0)  # File exists, size should be > 0 KB

        record = TranscriptionRecord(
            file_path="path.wav",
            file_name="path.wav",
            duration="00:10",
            transcription="Stats test"
        )
        self.repository.save(record)
        
        stats_with_record = self.repository.get_stats()
        self.assertEqual(stats_with_record["total_records"], 1)

    def test_clear_records(self):
        record = TranscriptionRecord(
            file_path="clear_me.wav",
            file_name="clear_me.wav",
            duration="00:10",
            transcription="Clear test"
        )
        self.repository.save(record)
        self.assertEqual(self.repository.get_stats()["total_records"], 1)
        
        self.repository.clear()
        self.assertEqual(self.repository.get_stats()["total_records"], 0)

if __name__ == "__main__":
    unittest.main()
