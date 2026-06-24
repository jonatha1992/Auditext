import sqlite3
import os
import shutil
import time
import config

DB_PATH = os.path.join(config.base_dir, "auditext.db")

def get_managed_audio_dir():
    path = os.path.join(config.base_dir, "data", "audios")
    os.makedirs(path, exist_ok=True)
    return path

def import_audio_to_system(src_path):
    if not src_path or not os.path.exists(src_path):
        return src_path
    
    managed_dir = get_managed_audio_dir()
    try:
        # Check if the file is already inside the managed directory
        if os.path.dirname(os.path.abspath(src_path)) == os.path.abspath(managed_dir):
            return src_path
            
        # Generate unique name
        base_name = os.path.basename(src_path)
        timestamp = int(time.time())
        unique_name = f"{timestamp}_{base_name}"
        dest_path = os.path.join(managed_dir, unique_name)
        
        shutil.copy2(src_path, dest_path)
        config.logger.info(f"Archivo de audio copiado al sistema: {dest_path}")
        return dest_path
    except Exception as e:
        config.logger.error(f"Error al copiar archivo de audio al sistema: {e}")
        return src_path


def migrate_existing_paths():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcripciones'")
        if not cursor.fetchone():
            conn.close()
            return
            
        cursor.execute("SELECT file_path FROM transcripciones")
        rows = cursor.fetchall()
        
        managed_dir = get_managed_audio_dir()
        audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
        updated = False
        
        for (path,) in rows:
            if not path or not os.path.exists(path):
                continue
            
            _, ext = os.path.splitext(path.lower())
            if ext not in audio_extensions:
                continue
                
            # If the file path is not inside the managed directory, migrate it!
            if os.path.dirname(os.path.abspath(path)) != os.path.abspath(managed_dir):
                base_name = os.path.basename(path)
                timestamp = int(time.time())
                unique_name = f"{timestamp}_{base_name}"
                dest_path = os.path.join(managed_dir, unique_name)
                
                try:
                    shutil.copy2(path, dest_path)
                    cursor.execute(
                        "UPDATE transcripciones SET file_path = ? WHERE file_path = ?",
                        (dest_path, path)
                    )
                    config.logger.info(f"Migrado archivo de historial antiguo: {path} -> {dest_path}")
                    updated = True
                except Exception as e:
                    config.logger.error(f"Error al migrar archivo {path}: {e}")
                    
        if updated:
            conn.commit()
        conn.close()
    except Exception as e:
        config.logger.error(f"Error durante la migración de rutas físicas: {e}")


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
        
        # Migrar audios de registros previos a la carpeta local
        migrate_existing_paths()
    except Exception as e:
        config.logger.error(f"Error al inicializar la base de datos: {e}")


def save_transcription(file_path, file_name, duration, transcription, summary="", language=""):
    try:
        # Import audio file to local system if it exists on disk and is an audio file
        if file_path and os.path.exists(file_path):
            _, ext = os.path.splitext(file_path.lower())
            audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".aac", ".opus"}
            if ext in audio_extensions:
                file_path = import_audio_to_system(file_path)

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
        
        # 1. Try exact match
        cursor.execute("""
            SELECT file_name, duration, transcription, summary, language 
            FROM transcripciones WHERE file_path = ?
        """, (file_path,))
        row = cursor.fetchone()
        
        # 2. Try match by suffix if exact match fails and it is an external path
        if not row and file_path:
            base_name = os.path.basename(file_path)
            cursor.execute("""
                SELECT file_name, duration, transcription, summary, language, file_path 
                FROM transcripciones
            """)
            all_rows = cursor.fetchall()
            for r in all_rows:
                db_path = r[5]
                # If the filename in DB ends with _basename (our unique prefix format)
                if db_path and os.path.basename(db_path).endswith(f"_{base_name}"):
                    row = r[:5]
                    break
                    
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
        # Delete local physical file if it exists inside the managed directory
        if file_path:
            managed_dir = get_managed_audio_dir()
            try:
                if os.path.dirname(os.path.abspath(file_path)) == os.path.abspath(managed_dir):
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        config.logger.info(f"Archivo de audio físico eliminado: {file_path}")
            except Exception as fe:
                config.logger.error(f"Error al intentar borrar archivo físico {file_path}: {fe}")

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

