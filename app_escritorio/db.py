import sqlite3
import os
import config

DB_PATH = os.path.join(config.base_dir, "auditext.db")

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
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
        config.logger.info("Base de datos SQLite inicializada exitosamente.")
    except Exception as e:
        config.logger.error(f"Error al inicializar la base de datos: {e}")

def save_transcription(file_path, file_name, duration, transcription, summary="", language=""):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO transcripciones 
            (file_path, file_name, duration, transcription, summary, language)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (file_path, file_name, duration, transcription, summary, language))
        conn.commit()
        conn.close()
        config.logger.info(f"Transcripción guardada en la base de datos para: {file_name}")
    except Exception as e:
        config.logger.error(f"Error al guardar transcripción: {e}")

def get_transcription(file_path):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_name, duration, transcription, summary, language 
            FROM transcripciones WHERE file_path = ?
        """, (file_path,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "file_name": row[0],
                "duration": row[1],
                "transcription": row[2],
                "summary": row[3],
                "language": row[4]
            }
        return None
    except Exception as e:
        config.logger.error(f"Error al obtener transcripción: {e}")
        return None

def update_summary(file_path, summary):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE transcripciones SET summary = ? WHERE file_path = ?
        """, (summary, file_path))
        conn.commit()
        conn.close()
        config.logger.info(f"Resumen actualizado en la base de datos para: {file_path}")
    except Exception as e:
        config.logger.error(f"Error al actualizar el resumen: {e}")

def get_connection():
    return sqlite3.connect(DB_PATH)

def get_total_transcriptions_count():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM transcripciones")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        config.logger.error(f"Error al obtener cantidad de transcripciones: {e}")
        return 0

def get_all_transcriptions():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT file_path, file_name, duration, transcription, summary, language, created_at 
            FROM transcripciones ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "file_path": row[0],
                "file_name": row[1],
                "duration": row[2],
                "transcription": row[3],
                "summary": row[4],
                "language": row[5],
                "created_at": row[6]
            }
            for row in rows
        ]
    except Exception as e:
        config.logger.error(f"Error al obtener todas las transcripciones: {e}")
        return []

def rename_transcription(file_path: str, new_name: str) -> bool:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE transcripciones SET file_name = ? WHERE file_path = ?",
            (new_name, file_path)
        )
        conn.commit()
        conn.close()
        config.logger.info("Transcripción renombrada: %s -> %s", file_path, new_name)
        return True
    except Exception as e:
        config.logger.error("Error al renombrar transcripción: %s", e)
        return False


def delete_transcription(file_path):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transcripciones WHERE file_path = ?", (file_path,))
        conn.commit()
        conn.close()
        config.logger.info(f"Transcripción eliminada para: {file_path}")
        return True
    except Exception as e:
        config.logger.error(f"Error al eliminar transcripción: {e}")
        return False
