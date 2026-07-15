# ADR 001 — Separar Entrevista de En vivo

## Estado

Propuesto.

## Decisión

Entrevista será una sección principal independiente. En vivo conservará solamente la captura, transcripción, grabación y resumen en tiempo real.

## Evidencia actual

`presentation/views/live_frame.py` concentra dos experiencias dentro de `LiveFrame`: la transcripción general y un panel condicional activado por `interview_var`. El modo Entrevista también modifica idioma, fuente, conexión a Gemini, contexto y presentación de sugerencias.

## Motivos

- Los usuarios ingresan con objetivos distintos.
- Entrevista necesita preparación y estados propios.
- La función es difícil de descubrir como casilla secundaria.
- La pantalla actual combina demasiados controles y responsabilidades.
- La dependencia en Gemini y sus implicaciones de privacidad deben ser explícitas.

## Consecuencias

### Positivas

- Navegación y propósito más claros.
- Mejor espacio para contexto, pregunta y sugerencias.
- Errores y requisitos de Gemini aislados del flujo local.
- Evolución independiente de la experiencia de Entrevista.

### Costos y riesgos

- Debe extraerse estado actualmente acoplado a `LiveFrame`.
- Una extracción apresurada puede duplicar captura y controles.
- La navegación y las pruebas de interfaz requieren cambios.

## Restricción de implementación

Separar la presentación, no duplicar la infraestructura. `Transcriber`, `InterviewLiveSession`, captura de audio y gestión de claves deben reutilizarse o extraerse detrás de componentes compartidos.

El flujo de Entrevista usa dos entradas simultáneas: audio del sistema para el entrevistador y micrófono para el candidato. Las señales conservan etiquetas de rol; solamente el audio del entrevistador se envía a Gemini Live, mientras la voz del candidato se transcribe localmente y se aporta como texto al historial conversacional del coach.
