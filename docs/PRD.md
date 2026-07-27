# PRD — AudioText

## 1. Resumen del producto

AudioText es una aplicación de escritorio para Windows que transforma audio en texto, permite conservar y consultar transcripciones y ofrece asistencia privada durante entrevistas laborales.

El producto se organiza en cuatro experiencias principales:

1. **Archivos:** transcribir audios existentes.
2. **En vivo:** capturar y transcribir audio del sistema o del micrófono.
3. **Entrevista:** comprender al entrevistador y recibir sugerencias de respuesta en tiempo real.
4. **Historial:** buscar, revisar y exportar resultados guardados.

## 2. Problema

Las personas que trabajan con grabaciones o conversaciones en tiempo real necesitan obtener texto utilizable sin depender de herramientas complejas. Los candidatos hispanohablantes que participan en entrevistas en inglés tienen además otro problema: deben comprender la pregunta y formular una respuesta mientras la conversación continúa.

La implementación actual incluye Entrevista dentro de En vivo mediante una casilla. Esto mezcla dos objetivos distintos:

- **En vivo:** producir una transcripción fiel.
- **Entrevista:** asistir al candidato para que comprenda y responda.

## 3. Usuarios objetivo

| Usuario | Necesidad principal |
|---|---|
| Profesional o estudiante | Transcribir grabaciones y clases. |
| Persona que documenta reuniones | Capturar conversaciones en tiempo real. |
| Candidato hispanohablante | Recibir apoyo durante una entrevista laboral en inglés. |

## 4. Objetivos

- Permitir transcripción local de archivos y audio en vivo.
- Reducir el esfuerzo para revisar, guardar y exportar transcripciones.
- Ofrecer un espacio de Entrevista enfocado, discreto y fácil de preparar.
- Mantener claras las funciones offline y las que requieren servicios externos.

## 5. No objetivos

- Responder automáticamente en nombre del candidato.
- Reproducir las sugerencias del asistente por audio.
- Grabar o analizar una conversación sin conocimiento y autorización del usuario.
- Sustituir plataformas de videollamadas o sistemas de selección de personal.

## 6. Decisión de navegación

**Entrevista será una sección principal separada de En vivo.**

La separación se justifica porque Entrevista posee un propósito, una preparación, dependencias y resultados propios. Ocultarla detrás de una casilla dificulta descubrirla y obliga a mostrar controles de transcripción que no son relevantes para el candidato.

Navegación propuesta:

1. Archivos
2. En vivo
3. Entrevista
4. Historial
5. Ajustes

La separación es de experiencia y presentación. La captura de audio y los servicios compartidos deben reutilizarse; no se debe duplicar el motor técnico.

## 7. Requisitos funcionales

### 7.1 Archivos

- Seleccionar uno o más archivos compatibles.
- Transcribir sin conexión mediante el modelo local.
- Mostrar progreso y errores recuperables.
- Guardar, resumir y exportar el resultado.

### 7.2 En vivo

- Elegir audio del sistema, micrófono o una fuente disponible.
- Seleccionar idioma o detección automática.
- Activar o desactivar la grabación de audio.
- Ver la transcripción mientras se captura.
- Guardar y resumir la sesión.
- No mostrar configuración ni sugerencias de Entrevista.

### 7.3 Entrevista

#### Preparación

- Permitir pegar el CV y datos del puesto o empresa.
- Explicar que Gemini y conexión a Internet son necesarios.
- Confirmar que existe una clave configurada antes de iniciar.
- Usar audio del sistema como fuente principal del entrevistador.

#### Sesión activa

- Transcribir en inglés lo dicho por el entrevistador.
- Capturar el micrófono del candidato en paralelo y transcribir su respuesta localmente.
- Identificar cada intervención como Entrevistador o Candidato sin mezclar las señales.
- Mantener un historial breve de ambos participantes para conservar el hilo de la conversación.
- Mostrar una glosa clara de la pregunta en español.
- Generar exactamente dos sugerencias breves en inglés.
- Destacar una respuesta recomendada, ideas clave personalizadas y una frase puente para ganar tiempo o pedir aclaración.
- Permitir copiar cada sugerencia con una acción explícita.
- Mantener silenciada la respuesta de audio del modelo.
- Mostrar estados de conexión, generación, rotación de clave y error.
- Permitir detener la sesión inmediatamente.

#### Cierre

- Permitir guardar la transcripción y la información útil de la sesión.
- No guardar CV, contexto sensible ni audio de forma implícita.
- Informar claramente qué datos se conservarán antes de guardar.

### 7.4 Historial

- Buscar transcripciones guardadas.
- Revisar texto, resumen, idioma y metadatos disponibles.
- Reproducir el audio cuando exista.
- Exportar resultados en formatos compatibles.

### 7.5 Ajustes

- Configurar modelo local, carpetas y base de datos.
- Configurar integraciones externas sin exponer las claves.
- Diferenciar funciones locales de funciones en línea.

## 8. Privacidad y seguridad

- La transcripción local debe funcionar sin enviar audio a servicios externos.
- Entrevista y resumen deben indicar que utilizan Gemini antes de transmitir datos.
- Las claves no deben aparecer en logs, interfaz, historial ni exportaciones.
- El usuario debe controlar la grabación y persistencia del audio.
- La aplicación debe recordar al usuario que cumpla las leyes y políticas aplicables a la grabación de conversaciones.

## 9. Requisitos no funcionales

- **Plataforma:** Windows 10 o superior.
- **Disponibilidad:** Archivos, En vivo e Historial deben conservar su flujo principal sin Gemini.
- **Rendimiento:** La interfaz no debe bloquearse durante captura, transcripción o solicitudes de IA.
- **Recuperación:** Un error de red o cuota debe mostrar un estado accionable y permitir detener o reintentar.
- **Accesibilidad:** Estados y acciones no deben depender únicamente del color.

## 10. Criterios de aceptación para separar Entrevista

- [ ] Entrevista aparece como destino propio en la navegación principal.
- [ ] En vivo ya no contiene la casilla “Modo entrevista”.
- [ ] La sección Entrevista contiene preparación, sesión activa y cierre.
- [ ] La pantalla muestra por separado lo que dice el entrevistador y lo que responde el candidato.
- [ ] Los flujos En vivo y Entrevista reutilizan la captura y los servicios existentes.
- [ ] El usuario conoce las dependencias en línea y el tratamiento de datos antes de iniciar.
- [ ] Un fallo de Gemini no afecta la transcripción local de Archivos o En vivo.
- [ ] Las pruebas existentes del servicio de entrevista continúan pasando.
- [ ] Se agregan pruebas para el cambio de navegación y los estados principales del nuevo flujo.

## 11. Métricas iniciales

- Porcentaje de sesiones de Entrevista que llegan al estado activo.
- Tiempo desde la pregunta detectada hasta las sugerencias visibles.
- Porcentaje de errores recuperables por red, modelo o cuota.
- Porcentaje de sugerencias copiadas durante una sesión.

Las métricas no deben registrar audio, CV, preguntas ni respuestas sin consentimiento explícito.

## 12. Entrega incremental

1. Extraer la interfaz de Entrevista desde `LiveFrame` sin cambiar el servicio actual.
2. Incorporar el nuevo destino en la navegación.
3. Separar el estado y ciclo de vida de ambas pantallas.
4. Definir persistencia y cierre de sesión con privacidad explícita.
5. Validar el flujo completo y retirar la casilla anterior.
