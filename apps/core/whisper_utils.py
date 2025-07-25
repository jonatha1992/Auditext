import whisper

def transcribe_audio(file_path):
    print('Cargando modelo Whisper (medium)...')
    model = whisper.load_model("medium")
    print('Modelo Whisper cargado.')
    result = model.transcribe(file_path)
    return result['text'] 