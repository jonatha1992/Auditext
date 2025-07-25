from django.urls import path
from . import views

urlpatterns = [
    path('test/', views.test_view, name='test'),
    path('api/transcribe/', views.transcribe_api, name='transcribe_api'),
    path('', views.transcribe_view, name='transcribe'),
] 