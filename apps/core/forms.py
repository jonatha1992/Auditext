from django import forms

class AudioUploadForm(forms.Form):
    audio_file = forms.FileField(label='Archivo de audio') 