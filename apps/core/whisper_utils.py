import whisper

# Cargar el modelo Medium una sola vez
model = whisper.load_model('medium')

def transcribe_audio(file_path):
    result = model.transcribe(file_path)
    return result['text'] 