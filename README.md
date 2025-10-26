# AudioText - Transcriptor y Traductor de Audio

**AudioText** es una aplicación de transcripción de audio que permite convertir archivos de audio a texto de manera eficiente y precisa. El sistema utiliza reconocimiento de voz avanzado y tecnologías de procesamiento de audio para convertir contenido hablado en texto.

## 🔄 Estado del Proyecto: En Migración

**La aplicación web está siendo migrada de Django a una arquitectura SSR (Server-Side Rendering) moderna.**

### Versiones Disponibles:
- **Aplicación de Escritorio**: Versión estable con interfaz gráfica completa (recomendada)
- **Aplicación Web (Django)**: Versión legacy en modo de prueba
- **Nueva Web (SSR)**: En desarrollo activo (próximamente)

## 💾 Descargar Aplicación de Escritorio

**¿Prefieres usar AudioText sin instalación?**

Descarga el prototipo de escritorio listo para usar:

**[📥 Descargar Prototipo de Escritorio](https://drive.google.com/drive/folders/1xQjfPNuMF6r0xZRcek96iU9X9TViqP21?usp=sharing)**

Esta versión incluye:
- ✅ Interfaz gráfica standalone (no requiere instalación de Python)
- ✅ Todas las dependencias incluidas
- ✅ Listo para ejecutar en Windows
- ✅ Procesamiento local de audio
- ✅ Funcionalidad completa de transcripción

## ⚠️ Versión Web Django (Legacy)

**La versión web Django está en modo de mantenimiento mientras se completa la migración a SSR.**

### Estado actual:
- ✅ Sistema de autenticación deshabilitado para pruebas
- ✅ Funcionalidades básicas de transcripción operativas
- ⚠️ No se recomienda para producción
- 🔄 Será reemplazada por la nueva arquitectura SSR

### Recomendación:
Para uso en producción, utiliza la **aplicación de escritorio** mientras se completa la migración web.

## 🚀 Características Principales

- **Transcripción de Audio**: Convierte archivos de audio a texto usando Google Speech Recognition
- **Traducción Automática**: Traduce las transcripciones entre múltiples idiomas
- **Procesamiento Asíncrono**: Utiliza Celery para procesar archivos grandes en segundo plano
- **Mejoramiento de Audio**: Aplica filtros de audio para mejorar la calidad de la transcripción
- **Detección de Actividad de Voz (VAD)**: Segmenta automáticamente el audio basándose en silencios
- **Interfaz Web Moderna**: Desarrollada con Bootstrap 5 para una experiencia de usuario óptima
- **Exportación de Resultados**: Guarda las transcripciones en archivos de texto
- **Panel de Control**: Visualización de estadísticas y gestión de archivos

## 📋 Idiomas Soportados

- Español (es-ES)
- Inglés (en-US)
- Francés (fr-FR)
- Alemán (de-DE)
- Italiano (it-IT)

## 🔧 Requisitos del Sistema

### Dependencias de Python
```
django
python-decouple
Pillow
django-crispy-forms
crispy-bootstrap5
whitenoise
gunicorn
celery
redis
speech_recognition
pydub
googletrans
mutagen
webrtcvad
```

### Software Externo
- **FFmpeg**: Requerido para el procesamiento de audio
- **Redis**: Para tareas en segundo plano con Celery

## 📦 Instalación

### Opción 1: Aplicación de Escritorio (Recomendada)

**No requiere instalación de Python ni dependencias.**

1. Descarga el ejecutable desde [Google Drive](https://drive.google.com/drive/folders/1xQjfPNuMF6r0xZRcek96iU9X9TViqP21?usp=sharing)
2. Extrae el archivo ZIP
3. Ejecuta `AudioText.exe`

### Opción 2: Desde Código Fuente (Aplicación de Escritorio)

```bash
git clone https://github.com/jonatha1992/AudioText.git
cd AudioText/app_escritorio
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
pip install -r ../requirements.txt
python Main.py
```

### Opción 3: Versión Web Django (Legacy - Solo para pruebas)

⚠️ **Nota**: Esta versión está en mantenimiento. Se recomienda usar la aplicación de escritorio.

```bash
git clone https://github.com/jonatha1992/AudioText.git
cd AudioText
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py runserver
```

Accede en `http://localhost:8000` (no requiere autenticación en modo de prueba)

## 🎯 Uso

### Aplicación de Escritorio (Recomendada)

1. Ejecuta `AudioText.exe` o `python Main.py`
2. Haz clic en "Seleccionar Audios" para cargar uno o varios archivos
3. Los archivos se procesan automáticamente:
   - Segmentación por VAD (detección de actividad de voz)
   - Mejoramiento de audio con filtros
   - Transcripción con Google Speech Recognition
4. Reproduce los audios y revisa las transcripciones
5. Exporta los resultados a CSV

**Formatos soportados**: WAV, MP3, OGG, M4A, AAC, MP4

### Interfaz Web Django (Legacy)

⚠️ Solo para pruebas de desarrollo

1. Accede a `http://localhost:8000`
2. Sube archivos de audio (no requiere login)
3. Selecciona el modelo Whisper (small/medium/large)
4. Procesa y revisa las transcripciones

## 🛠️ Arquitectura del Sistema

### Componentes Principales

- **`models.py`**: Definición de modelos para archivos de audio, transcripciones y traducciones
- **`views.py`**: Vistas para la interfaz de usuario y API
- **`audio_processing.py`**: Funciones para procesamiento de audio y transcripción
- **`tasks.py`**: Tareas asíncronas de Celery para procesamiento en segundo plano

### Tecnologías Utilizadas

#### Aplicación de Escritorio (Actual)
- **GUI**: Tkinter
- **Reconocimiento de Voz**: Google Speech Recognition API
- **Procesamiento de Audio**: PyDub con FFmpeg
- **Filtrado Digital**: SciPy para mejoramiento de calidad
- **Empaquetado**: PyInstaller

#### Aplicación Web Legacy (Django)
- **Framework Backend**: Django
- **Frontend**: Bootstrap 5, JavaScript
- **Tareas Asíncronas**: Celery con Redis
- **Estado**: En mantenimiento, será reemplazada

#### Nueva Aplicación Web (SSR - En desarrollo)
- **Arquitectura**: Server-Side Rendering
- **Estado**: En desarrollo activo
- **Detalles**: Por anunciar

## ⚙️ Configuración Avanzada

### Parámetros de Audio
- **Umbral de Energía**: Configurable para diferentes calidades de audio
- **Detección de Silencio**: Ajustable según las características del audio
- **Filtros de Frecuencia**: Optimizados para voz humana (300-3000 Hz)

### Optimización de Rendimiento
- **Procesamiento en Segundo Plano**: Celery para tareas asíncronas
- **Segmentación Inteligente**: División automática basada en VAD
- **Gestión de Memoria**: Procesamiento eficiente de archivos grandes

## 📈 Estado del Proyecto

- **Versión de Escritorio**: 1.0.0 (Estable - Recomendada)
- **Versión Web Django**: Legacy (En mantenimiento)
- **Versión Web SSR**: En desarrollo
- **Último Update**: Octubre 2025
- **Desarrollador**: Correa Jonathan

### Roadmap
- [x] Aplicación de escritorio funcional
- [x] Versión web Django básica
- [ ] Migración a arquitectura SSR moderna
- [ ] API REST unificada
- [ ] Aplicación móvil (futuro)

## 🤝 Contribuir

Las contribuciones son bienvenidas. Para contribuir:

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit tus cambios (`git commit -m 'Add some AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abre un Pull Request

## 📝 Licencia

Este proyecto está desarrollado por **Correa Jonathan** © 2025. 

## 🆘 Soporte

Si encuentras algún problema o necesitas ayuda:
- Abre un [issue](https://github.com/jonatha1992/AudioText/issues) en GitHub
- Contacta con nosotros a través del formulario de contacto en la aplicación

---

**AudioText** - Transformando audio en texto de manera inteligente y eficiente.