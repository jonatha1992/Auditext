# AudioText - Transcriptor y Traductor de Audio

**AudioText** es una aplicación web desarrollada con Django que permite transcribir y traducir archivos de audio de manera eficiente y precisa. El sistema utiliza reconocimiento de voz avanzado y tecnologías de procesamiento de audio para convertir contenido hablado en texto.

## ⚠️ MODO DE PRUEBA TEMPORAL

**Esta versión tiene el sistema de inicio de sesión y registro temporalmente deshabilitado para permitir pruebas rápidas del sistema de transcripción.**

### Cambios temporales realizados:
- ✅ Eliminado el flujo de inicio de sesión y registro
- ✅ La URL raíz (/) redirige directamente a la página de subir archivos
- ✅ Todas las funcionalidades de carga, procesamiento y transcripción funcionan sin autenticación
- ✅ Navegación simplificada para acceso directo a las funciones principales
- ✅ Se usa un usuario de prueba automáticamente para todas las operaciones

### Navegación disponible:
- **Subir Audio**: Sube archivos de audio para transcribir
- **Archivos de Audio**: Ve todos los archivos subidos
- **Panel**: Estadísticas generales del sistema
- **Acerca de**: Información sobre la aplicación
- **Contacto**: Formulario de contacto

### Para restaurar la autenticación:
1. Descomentar las líneas relacionadas con autenticación en `views.py`
2. Restaurar `LoginRequiredMixin` y `@login_required` en las vistas
3. Descomentar las URLs de autenticación en `myproject/urls.py`
4. Restaurar la configuración de login en `settings.py`
5. Restaurar el contenido condicional en las plantillas

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

1. Clona este repositorio:
```bash
git clone https://github.com/jonatha1992/AudioText.git
cd AudioText
```

2. Crea un entorno virtual e instala las dependencias:
```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Configura las variables de entorno:
```bash
cp .env.example .env
# Edita el archivo .env con tus configuraciones
```

4. Aplica las migraciones:
```bash
python manage.py migrate
```

5. Crea un superusuario:
```bash
python manage.py createsuperuser
```

6. Inicia el servidor:
```bash
python manage.py runserver
```

7. En otra terminal, inicia el worker de Celery:
```bash
celery -A django_project worker -l info
```

## 🎯 Uso

### Interfaz Web

1. Accede a la aplicación en `http://localhost:8000`
2. Inicia sesión con tu cuenta
3. Ve a "Subir Audio" y sube un archivo de audio
4. Configura los parámetros de transcripción
5. Inicia el proceso de transcripción
6. Una vez completada, puedes ver, traducir o descargar la transcripción

### Panel de Administración

1. Accede al panel de administración en `http://localhost:8000/admin`
2. Gestiona usuarios, archivos de audio, transcripciones y traducciones

## 🛠️ Arquitectura del Sistema

### Componentes Principales

- **`models.py`**: Definición de modelos para archivos de audio, transcripciones y traducciones
- **`views.py`**: Vistas para la interfaz de usuario y API
- **`audio_processing.py`**: Funciones para procesamiento de audio y transcripción
- **`tasks.py`**: Tareas asíncronas de Celery para procesamiento en segundo plano

### Tecnologías Utilizadas

- **Framework Web**: Django
- **Reconocimiento de Voz**: Google Speech Recognition API
- **Procesamiento de Audio**: PyDub con FFmpeg
- **Filtrado Digital**: SciPy para mejoramiento de calidad
- **Traducción**: Google Translate API
- **Frontend**: Bootstrap 5, JavaScript
- **Tareas Asíncronas**: Celery con Redis

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

- **Versión**: 1.0.0
- **Estado**: Estable
- **Último Update**: Julio 2025
- **Desarrollador**: Correa Jonathan

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