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
