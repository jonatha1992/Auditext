from django.urls import path
from . import views, simple_views

app_name = 'core'

urlpatterns = [
    # Páginas principales - Nueva versión sin base de datos
    path('', simple_views.SimpleHomeView.as_view(), name='home'),
    path('transcribir/', simple_views.SimpleTranscribeView.as_view(), name='simple_transcribe'),
    path('sobre-nosotros/', simple_views.SimpleAboutView.as_view(), name='sobre_nosotros'),
    path('descargar-transcripcion/', simple_views.download_transcription_text, name='download_transcription_text'),
    
    # URLs originales comentadas para preservar funcionalidad
    # path('contacto/', views.ContactView.as_view(), name='contacto'),
    # path('contacto/gracias/', views.ContactThanksView.as_view(), name='contacto_gracias'),
    # path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    # path('perfil/', views.UserProfileView.as_view(), name='profile'),
    # path('audio/', views.AudioFileListView.as_view(), name='audio_list'),
    # path('audio/subir/', views.AudioFileUploadView.as_view(), name='audio_upload'),
    # path('audio/<int:pk>/', views.AudioFileDetailView.as_view(), name='audio_detail'),
    # path('audio/<int:pk>/eliminar/', views.AudioFileDeleteView.as_view(), name='audio_delete'),
    # path('audio/<int:audio_id>/transcribir/', views.TranscriptionCreateView.as_view(), name='transcription_create'),
    # path('transcripcion/<int:pk>/', views.TranscriptionDetailView.as_view(), name='transcription_detail'),
    # path('transcripcion/<int:pk>/estado/', views.check_transcription_status, name='transcription_status'),
    # path('transcripcion/<int:pk>/descargar/', views.download_transcription, name='transcription_download'),
    # path('transcripcion/<int:transcription_id>/traducir/', views.TranslationCreateView.as_view(), name='translation_create'),
    # path('traduccion/<int:pk>/', views.TranslationDetailView.as_view(), name='translation_detail'),
    # path('traduccion/<int:pk>/estado/', views.check_translation_status, name='translation_status'),
    # path('traduccion/<int:pk>/descargar/', views.download_translation, name='translation_download'),
]