import os
import sys

# Add app_escritorio to sys.path to resolve imports correctly
sys.path.insert(0, os.path.abspath("app_escritorio"))

import diarizer

def run_test():
    print("--- INICIANDO TEST DE DIARIZACIÓN ---")
    
    # Check backend availability
    pyannote_avail = diarizer._is_pyannote_available()
    simple_avail = diarizer._is_simple_available()
    
    print(f"Backend Pyannote/WhisperX disponible: {pyannote_avail}")
    print(f"Backend Simple Diarizer (Offline) disponible: {simple_avail}")
    
    # Default audio file for testing
    default_audio = "Audios/PRUEBA (2).wav"
    audio_path = sys.argv[1] if len(sys.argv) > 1 else default_audio
    
    if not os.path.exists(audio_path):
        print(f"ERROR: No se encontró el archivo de audio de prueba en: {audio_path}")
        return
        
    print(f"Procesando archivo: {audio_path}...")
    try:
        result = diarizer.diarize_file(audio_path)
        print("\n--- RESULTADO DE LA TRANSCRIPCIÓN CON DIARIZACIÓN ---")
        print(result)
        print("-----------------------------------------------------")
        print("¡TEST COMPLETADO CON ÉXITO!")
    except Exception as e:
        print(f"\nERROR: Falló la diarización. Detalle: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
