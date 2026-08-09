# PRD — AudioText

## 1. Resultado del producto

AudioText es una aplicación de escritorio para Windows que transcribe audio, conserva sesiones y ayuda al usuario a responder preguntas durante entrevistas, exámenes y llamadas. La asistencia debe ser rápida, breve, contextual y privada: nunca debe hablar ni enviar una respuesta automáticamente en nombre del usuario.

La navegación principal contiene seis experiencias:

1. **Archivos:** transcripción de audios existentes.
2. **En vivo:** captura y transcripción general.
3. **Entrevista:** apoyo para entrevistas laborales y conversaciones.
4. **Resolver:** respuestas para exámenes, prácticas orales y preguntas académicas.
5. **Historial:** consulta, reproducción y exportación.
6. **Ajustes:** configuración local e integraciones.

## 2. Problema

Durante una llamada o examen oral, el usuario necesita escuchar una pregunta, comprenderla y formular una respuesta mientras la conversación continúa. Una herramienta útil debe:

- captar la pregunta desde el audio del sistema;
- conservar la pregunta como una sola intervención, sin separar palabras o sílabas;
- responder exactamente lo preguntado y mantenerse dentro de la materia;
- entregar texto corto y listo para decir;
- capturar y guardar también la respuesta oral del usuario;
- continuar funcionando cuando un proveedor de IA alcanza su cuota.

Entrevista laboral y Resolver son objetivos diferentes. No deben compartir nombres, instrucciones ni resultados aunque reutilicen infraestructura.

## 3. Usuarios

| Usuario | Necesidad |
|---|---|
| Estudiante | Resolver una pregunta de examen usando el programa o material de una materia. |
| Persona en práctica oral | Ser examinada por un profesor IA, responder oralmente y recibir una devolución. |
| Candidato laboral | Recibir apoyo contextual durante una entrevista. |
| Profesional | Transcribir archivos, reuniones y llamadas. |

## 4. Principios del producto

- **Respuesta antes que explicación:** mostrar primero una respuesta utilizable.
- **Corto y al pie:** una respuesta principal, sin introducciones ni información lateral.
- **Contexto estable:** conservar el rol seleccionado y no convertir un examen en entrevista o práctica de inglés.
- **Interpretar sin inventar:** reconstruir errores y elipsis de alta confianza; si faltan conceptos esenciales o el texto queda fuera del material, esperar otro fragmento o solicitar repetición.
- **Baja latencia:** evitar reintentos de proveedores ya agotados.
- **Control del usuario:** el usuario decide cuándo grabar, guardar, copiar o reproducir.

## 5. No objetivos

- Hablar, contestar una llamada o enviar mensajes automáticamente.
- Inventar definiciones para compensar una mala transcripción.
- Mezclar Entrevista, práctica de idiomas y Resolver en una misma experiencia.
- Grabar conversaciones sin conocimiento y autorización del usuario.
- Sustituir una plataforma de videollamadas o un sistema educativo.

## 6. Requisitos funcionales

### 6.1 Archivos

- Seleccionar uno o más audios compatibles.
- Transcribir localmente con Whisper.
- Mostrar progreso y errores recuperables.
- Guardar, resumir y exportar resultados.

### 6.2 En vivo

- Capturar audio del sistema, micrófono o aplicación disponible.
- Seleccionar idioma o detección automática.
- Activar o desactivar la grabación.
- Mostrar y guardar la transcripción.
- Generar un resumen opcional.
- No mostrar controles de Entrevista o Resolver.

### 6.3 Entrevista

Modos permitidos:

- Entrevista laboral.
- Práctica de idioma.
- Conversación general.

Requisitos:

- Aceptar CV, puesto, empresa o contexto de conversación.
- Escuchar al entrevistador desde el audio del sistema.
- Capturar la respuesta del usuario desde un micrófono separado.
- Mantener roles de Entrevistador y Candidato.
- Responder en el idioma configurado.
- Mostrar una respuesta principal breve y lista para decir.
- Conservar un historial corto para sostener el hilo.
- Permitir copiar la respuesta sin reproducirla automáticamente.

#### Simulacro de entrevista

- Permitir elegir entre asistencia para una entrevista real y un simulacro conducido por IA.
- Generar una pregunta por turno a partir del puesto, empresa, CV o contexto indicado.
- Capturar la respuesta oral del candidato y permitir que confirme explícitamente cuándo terminó.
- Evaluar la respuesta y decidir entre profundizar con una repregunta, avanzar a otro tema o finalizar.
- Limitar la cantidad de preguntas y la profundidad de repreguntas para asegurar un cierre predecible.
- Evitar preguntas duplicadas y conservar los temas ya cubiertos durante la sesión.
- Permitir finalizar el simulacro manualmente en cualquier momento.
- Mostrar al finalizar una devolución con fortalezas, aspectos para mejorar y una recomendación concreta.
- La reproducción por voz de las preguntas es opcional y no debe activar automáticamente la escucha del micrófono mientras suena.
- El primer incremento no detecta automáticamente el final de una respuesta por silencio.

### 6.4 Resolver

Resolver es un módulo principal separado de Entrevista y de Práctica oral.

Modos permitidos:

- **Resolver preguntas:** una respuesta completa y directa.
- **Examen oral:** respuesta breve lista para decir.

#### Preparación

- Seleccionar audio del sistema para captar una llamada o videoconferencia.
- Seleccionar el micrófono del usuario.
- Pegar programa, cronograma, temario o apuntes.
- Cargar documentos locales compatibles.
- Elegir un cuaderno/materia de NotebookLM.
- Sincronizar una guía compacta del cuaderno antes de iniciar.

### 6.5 Práctica oral con profesor IA

Práctica oral es un destino principal independiente. La IA ocupa el rol de profesor;
no escucha audio del sistema ni espera una pregunta externa.

- Mostrar NotebookLM como fuente principal y activada por defecto.
- Permitir conectar la cuenta, actualizar la lista y elegir una materia desde la misma vista.
- Generar las preguntas únicamente con la materia sincronizada, salvo que el usuario active también el contexto escrito.

- No capturar audio del sistema: la pregunta se origina dentro de la aplicación.
- Formular una pregunta académica por turno usando únicamente el material preparado.
- Capturar la respuesta del estudiante desde el micrófono.
- Mostrar la transcripción de la respuesta mientras el estudiante habla.
- Ofrecer **Dame una pista** sin revelar la solución ni avanzar el turno.
- Ofrecer **Leer pregunta** y pausar el micrófono durante la reproducción.
- Confirmar el final mediante **Terminé mi respuesta**, sin inferirlo por una pausa breve.
- Evaluar corrección conceptual, fortalezas y omisiones.
- Elegir entre repreguntar, avanzar a otro tema o finalizar.
- Reanudar el micrófono después de cada devolución para evitar una sesión aparentemente bloqueada.
- Rechazar preguntas duplicadas y pedir al modelo una pregunta diferente.
- Negociar la frecuencia nativa del micrófono y remuestrear para el motor de reconocimiento.
- Mostrar al cierre fortalezas, aspectos para mejorar y una recomendación concreta.

#### Pregunta

- Conservar los espacios originales de la transcripción incremental.
- Unir fragmentos cortados dentro de una palabra.
- Usar cierre adaptativo: 0,65 segundos para preguntas terminadas, 1,2 segundos para consignas claras sin puntuación y hasta 4 segundos para texto ambiguo.
- Conservar como máximo dos fragmentos durante 7 segundos para reconstruir una intervención partida.
- No cerrar la pregunta durante una pausa natural o por eventos parciales del modelo.
- Reconocer preguntas, imperativos académicos y consignas elípticas como `diferenciame`, `comparación entre` o `X versus Y`.
- Corregir únicamente errores de transcripción respaldados por contexto suficiente y conservar siempre el texto original.
- Mostrar inmediatamente la pregunta detectada o interpretada como una oración continua.

#### Respuesta de IA

- Mantener siempre el modo y la materia seleccionados.
- Contestar exactamente la pregunta detectada.
- Devolver una sola respuesta principal.
- En Resolver: 2 a 3 oraciones y máximo 80 palabras.
- En los demás modos orales: máximo 2 oraciones y 55 palabras.
- Omitir introducciones, alternativas, consejos e información lateral.
- No traducir automáticamente al inglés en modos académicos.
- No definir términos desconocidos ausentes del material.
- Si la transcripción parece corrupta o fuera de contexto, indicar que debe repetirse.
- Reemplazar visualmente la respuesta anterior cuando comienza una pregunta nueva y mostrar un indicador de generación.
- Si una pregunta aceptada produce una respuesta vacía, reintentar una sola vez sin duplicar solicitudes activas.

#### Pausa y reanudación

- Pausar ambas fuentes de audio sin cerrar la sesión.
- Bloquear el envío de audio mientras la sesión está pausada.
- Descartar la intervención incompleta al pausar para evitar mezclarla con la siguiente.
- Reanudar con un nuevo límite de turno y conservar respuestas ya generadas.

#### Respuesta del usuario

- Capturar el micrófono en paralelo.
- Resolver diferencias de nombres entre `soundcard` y `sounddevice`.
- Probar el siguiente endpoint físico si el actual devuelve audio exactamente vacío.
- Transcribir localmente la voz del usuario.
- Mostrarla bajo **TU RESPUESTA**.
- Incorporarla al historial conversacional y a la sesión guardada.
- Mezclarla en el audio final solamente cuando **Guardar audio** esté activado.

### 6.5 NotebookLM

- Mostrar los cuadernos disponibles como materias seleccionables.
- No consultar NotebookLM en cada pregunta en vivo.
- Preparar antes de la sesión un contexto compacto para reducir latencia.
- Informar estados de autenticación, carga, selección y error.
- Permitir continuar con material pegado si NotebookLM no está disponible.

### 6.6 Proveedores de IA

Orden de respaldo para generación de texto:

1. Gemini API 1.
2. Gemini API 2.
3. Gemini API 3.
4. NVIDIA API 1.
5. NVIDIA API 2.

Reglas:

- Una clave agotada no debe probarse nuevamente en cada pregunta de la misma sesión.
- NVIDIA debe rotar a su segunda clave ante cuota, autenticación o error recuperable.
- El estado debe mostrar módulo, proveedor activo y respaldo disponible.
- Gemini Live continúa siendo el canal de transcripción de audio en los modos asistidos.
- NVIDIA se utiliza para generar texto una vez disponible la pregunta transcripta.
- Resúmenes también deben utilizar NVIDIA cuando Gemini no esté disponible.

### 6.7 Historial y guardado

- Guardar pregunta, respuesta de IA y respuesta del usuario con títulos diferenciados.
- Registrar la sesión automáticamente al finalizar cuando exista texto o audio.
- Permitir guardado manual con un nombre elegido.
- Reproducir el audio si fue grabado.
- No guardar CV, programa completo ni credenciales dentro de la transcripción.

## 7. Estados visibles

Ejemplos:

- `Resolver activo · Gemini 1/3 · NVIDIA disponible (2)`
- `Resolver activo · NVIDIA · 2 claves`
- `Entrevista activa · Gemini 1/3 · NVIDIA disponible (2)`
- `Resolviendo pregunta…`
- `La pregunta parece haberse transcripto incorrectamente`
- `Guardada en Historial`
- `Error de micrófono: …`

Los estados no deben depender únicamente del color.

## 8. Privacidad y seguridad

- Archivos y transcripción local no deben requerir servicios externos.
- Audio del interlocutor puede enviarse a Gemini Live únicamente durante una sesión asistida iniciada por el usuario.
- El texto de la pregunta y el contexto relevante pueden enviarse a Gemini o NVIDIA para generar respuestas.
- Las claves nunca deben mostrarse en interfaz, logs, historial ni exportaciones.
- El usuario controla grabación y persistencia.
- La interfaz debe recordar el cumplimiento de leyes y políticas de grabación aplicables.

## 9. Requisitos no funcionales

| Área | Requisito |
|---|---|
| Plataforma | Windows 10 o superior. |
| Interfaz | No bloquearse durante captura, transcripción o generación. |
| Latencia | Mostrar la respuesta dentro de 4 segundos desde el cierre de una pregunta en condiciones normales. |
| Respaldo | Pasar directamente a NVIDIA cuando Gemini ya esté agotado en la sesión. |
| Audio | Procesar bloques al ritmo real; nunca ejecutar un bucle vacío de captura. |
| Recuperación | Mostrar errores accionables y permitir detener o reintentar. |
| Accesibilidad | Combinar texto, icono y color para los estados. |

## 10. Criterios de aceptación

### Implementados

- [x] Entrevista es un destino independiente.
- [x] Resolver es un destino independiente.
- [x] Entrevista no ofrece modos académicos.
- [x] Resolver no convierte preguntas académicas en práctica de inglés.
- [x] Práctica oral actúa como profesor IA y no como asistente de respuestas.
- [x] Examen oral conserva al profesor real como origen de las preguntas.
- [x] El micrófono se reanuda después de cada pregunta del profesor IA.
- [x] NotebookLM permite seleccionar una materia y sincronizar contexto.
- [x] Las transcripciones incrementales no insertan saltos entre sílabas.
- [x] Las pausas breves no crean múltiples preguntas.
- [x] Las consignas académicas imperativas y elípticas producen una pregunta interpretada.
- [x] La pregunta nueva se muestra antes de que llegue la respuesta y reemplaza el contenido anterior con un indicador de carga.
- [x] Una respuesta vacía se reintenta una sola vez.
- [x] Pausar y reanudar no mezcla fragmentos de preguntas diferentes.
- [x] Las respuestas son breves, directas y limitadas por palabras.
- [x] Gemini rota entre tres claves.
- [x] NVIDIA rota entre dos claves y actúa como respaldo.
- [x] Los proveedores agotados se omiten en los turnos siguientes.
- [x] Los resúmenes tienen respaldo NVIDIA.
- [x] La sesión guardada incluye **TU RESPUESTA** cuando el micrófono logra transcribirla.
- [x] El micrófono alternativo cambia de endpoint ante audio exactamente vacío.
- [x] Las claves permanecen ocultas.

### Simulacro de entrevista

- [x] El usuario puede iniciar un simulacro sin capturar audio del sistema.
- [x] La IA formula una pregunta inicial acorde al contexto configurado.
- [x] Cada respuesta confirmada produce evaluación y una repregunta, un cambio de tema o el cierre.
- [x] El simulacro termina al alcanzar el límite configurado o cuando el usuario lo finaliza.
- [x] El cierre muestra fortalezas, aspectos para mejorar y una recomendación concreta.
- [x] El modo de entrevista real conserva su comportamiento actual.

### Validación manual pendiente

- [ ] Confirmar tres preguntas consecutivas desde una videoconferencia real.
- [ ] Confirmar respuesta visible dentro del objetivo de 4 segundos.
- [ ] Confirmar transcripción y guardado de **TU RESPUESTA** con el micrófono elegido.
- [ ] Confirmar reproducción del audio combinado desde Historial.
- [ ] Confirmar recuperación al agotar Gemini y NVIDIA API 1.

## 11. Métricas

- Tiempo desde el final de la pregunta hasta la respuesta visible.
- Porcentaje de preguntas que producen una respuesta válida.
- Porcentaje de preguntas rechazadas por transcripción dudosa.
- Cambios de proveedor por sesión.
- Porcentaje de respuestas del usuario capturadas y guardadas.
- Errores de micrófono, audio vacío y pérdida de continuidad.

Las métricas no deben almacenar el contenido de audio, preguntas, respuestas, CV ni material académico.

## 12. Flujo de referencia

```text
Audio de llamada
      ↓
Gemini Live transcribe y agrupa la pregunta
      ↓
Detector semántico reconstruye intención y muestra la pregunta
      ↓
Contexto local / materia NotebookLM
      ↓
Gemini 1 → Gemini 2 → Gemini 3 → NVIDIA 1 → NVIDIA 2
      ↓
Respuesta breve en pantalla

Micrófono del usuario
      ↓
Transcripción local + grabación opcional
      ↓
TU RESPUESTA → Historial
```
