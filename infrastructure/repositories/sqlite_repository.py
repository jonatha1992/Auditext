import os
import sqlite3
import shutil
import time
from typing import Any
from core.interfaces.interfaces import TranscriptionRepository
from core.domain.entities import TranscriptionRecord
from config import logger

class SQLiteTranscriptionRepository(TranscriptionRepository):
    def __init__(self, db_path: str, logger_instance=logger):
        self.db_path = db_path
        self.logger = logger_instance

    def init_db(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcripciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE,
                    file_name TEXT,
                    duration TEXT,
                    transcription TEXT,
                    summary TEXT,
                    language TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            self.logger.info("Base de datos SQLite inicializada exitosamente.")
        except Exception as e:
            self.logger.error(f"Error al inicializar la base de datos: {e}")

    def get_managed_audio_dir(self) -> str:
        base_dir = os.path.dirname(self.db_path)
        path = os.path.join(base_dir, "data", "audios")
        os.makedirs(path, exist_ok=True)
        return path

    def import_audio_to_system(self, src_path: str) -> str:
        if not src_path or not os.path.exists(src_path):
            return src_path
        
        managed_dir = self.get_managed_audio_dir()
        try:
            if os.path.dirname(os.path.abspath(src_path)) == os.path.abspath(managed_dir):
                return src_path
                
            base_name = os.path.basename(src_path)
            timestamp = int(time.time())
            unique_name = f"{timestamp}_{base_name}"
            dest_path = os.path.join(managed_dir, unique_name)
            
            shutil.copy2(src_path, dest_path)
            self.logger.info(f"Archivo de audio copiado al sistema: {dest_path}")
            return dest_path
        except Exception as e:
            self.logger.error(f"Error al copiar archivo de audio al sistema: {e}")
            return src_path

    def save(self, record: TranscriptionRecord) -> str:
        saved_path = record.file_path
        try:
            if record.file_path and os.path.exists(record.file_path):
                _, ext = os.path.splitext(record.file_path.lower())
                audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
                if ext in audio_extensions:
                    saved_path = self.import_audio_to_system(record.file_path)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO transcripciones 
                (file_path, file_name, duration, transcription, summary, language)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (saved_path, record.file_name, record.duration, record.transcription, record.summary, record.language))
            conn.commit()
            conn.close()
            self.logger.info(f"Transcripción guardada en la base de datos para: {record.file_name}")
            return saved_path
        except Exception as e:
            self.logger.error(f"Error al guardar transcripción: {e}")
            return record.file_path

    def get(self, file_path: str) -> TranscriptionRecord | None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 1. Try exact match
            cursor.execute("""
                SELECT file_name, duration, transcription, summary, language, created_at, id
                FROM transcripciones WHERE file_path = ?
            """, (file_path,))
            row = cursor.fetchone()
            
            # 2. Try match by suffix if exact match fails
            if not row and file_path:
                base_name = os.path.basename(file_path)
                cursor.execute("""
                    SELECT file_name, duration, transcription, summary, language, created_at, id, file_path 
                    FROM transcripciones
                """)
                all_rows = cursor.fetchall()
                for r in all_rows:
                    db_path = r[7]
                    if db_path and os.path.basename(db_path).endswith(f"_{base_name}"):
                        row = r[:7]
                        file_path = db_path
                        break
                        
            conn.close()
            if row:
                return TranscriptionRecord(
                    file_path=file_path,
                    file_name=row[0],
                    duration=row[1],
                    transcription=row[2],
                    summary=row[3],
                    language=row[4],
                    created_at=row[5],
                    record_id=row[6]
                )
            return None
        except Exception as e:
            self.logger.error(f"Error al obtener transcripción para {file_path}: {e}")
            return None

    def get_all(self) -> list[TranscriptionRecord]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT file_path, file_name, duration, transcription, summary, language, created_at, id
                FROM transcripciones ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()
            return [
                TranscriptionRecord(
                    file_path=row[0],
                    file_name=row[1],
                    duration=row[2],
                    transcription=row[3],
                    summary=row[4],
                    language=row[5],
                    created_at=row[6],
                    record_id=row[7]
                )
                for row in rows
            ]
        except Exception as e:
            self.logger.error(f"Error al obtener todas las transcripciones: {e}")
            return []

    def delete(self, file_path: str) -> None:
        try:
            if file_path:
                managed_dir = self.get_managed_audio_dir()
                try:
                    if os.path.dirname(os.path.abspath(file_path)) == os.path.abspath(managed_dir):
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            self.logger.info(f"Archivo de audio físico eliminado: {file_path}")
                except Exception as fe:
                    self.logger.error(f"Error al intentar borrar archivo físico {file_path}: {fe}")

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transcripciones WHERE file_path = ?", (file_path,))
            conn.commit()
            conn.close()
            self.logger.info(f"Transcripción eliminada para: {file_path}")
        except Exception as e:
            self.logger.error(f"Error al eliminar transcripción: {e}")

    def rename(self, file_path: str, new_name: str) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE transcripciones SET file_name = ? WHERE file_path = ?", (new_name, file_path))
            conn.commit()
            conn.close()
            self.logger.info("Transcripción renombrada: %s -> %s", file_path, new_name)
            return True
        except Exception as e:
            self.logger.error("Error al renombrar transcripción: %s", e)
            return False

    def get_stats(self) -> dict[str, Any]:
        count = 0
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM transcripciones")
            count = cursor.fetchone()[0]
            conn.close()
        except Exception as e:
            self.logger.error(f"Error al obtener estadísticas de registros: {e}")

        size_kb = 0.0
        try:
            if os.path.exists(self.db_path):
                size_kb = os.path.getsize(self.db_path) / 1024.0
        except Exception as e:
            self.logger.error(f"Error al obtener tamaño del archivo de BD: {e}")

        return {
            "total_records": count,
            "size_kb": size_kb
        }

    def clear(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transcripciones")
            conn.commit()
            conn.close()
            self.logger.info("Base de datos de transcripciones vaciada completamente.")
        except Exception as e:
            self.logger.error(f"Error al vaciar la base de datos: {e}")

    def update_summary(self, file_path: str, summary: str) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE transcripciones SET summary = ? WHERE file_path = ?", (summary, file_path))
            conn.commit()
            conn.close()
            self.logger.info(f"Resumen actualizado para: {file_path}")
        except Exception as e:
            self.logger.error(f"Error al actualizar resumen: {e}")
