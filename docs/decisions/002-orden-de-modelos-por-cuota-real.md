# 002 — El orden de los modelos se decide con la cuota real, no con la versión

Fecha: 2026-08-06

## Contexto

La cadena de respaldo del resumen (`summarizer._FALLBACK_MODELS`) arrancaba con
`gemini-2.0-flash-lite`. La intuición era razonable: es un modelo más chico, así
que debería estar más disponible cuando el primario se satura.

Medido contra la API real con las keys en uso, no es así:

| Modelo | Estado |
|---|---|
| `gemini-2.0-flash-lite` | `429` — cuota 0 en free tier |
| `gemini-3.5-flash-lite` | vivo |
| `gemini-flash-lite-latest` | vivo |

El primer respaldo estaba **garantizado a fallar**. Cada vez que el primario se
agotaba, el primer reintento era latencia regalada antes de llegar a un modelo
que sí responde.

## Decisión

Ordenar la cadena por disponibilidad verificada, no por número de versión:

```python
_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.0-flash-lite",   # último: long shot para cuentas con cuota paga
]
```

El 2.0 queda al final en lugar de borrarse porque en una cuenta con cuota paga sí
sirve, y ahí no cuesta nada: sólo se lo llama cuando los dos anteriores fallaron.

## Consecuencias

- `RETIRED_MODELS` en `gemini_keys.py` sigue siendo el único lugar donde se
  filtran los modelos muertos, porque `GEMINI_MODEL` del `.env` alimenta coach,
  resumen y transcripción a la vez.
- Un `429` con cuota 0 es tan inservible como un `404`, pero se ve distinto en
  los logs. Conviene tratarlos igual al ordenar una cadena.
- La regla general: antes de mover un modelo en una cadena, medirlo con la key
  que lo va a usar. La versión del modelo no dice nada sobre su cuota.

## Nota sobre tokens de razonamiento

Relacionado, y vale acá porque afecta a cualquier salida estructurada:
`gemini-flash-latest` razona antes de responder y esos tokens
(`thoughtsTokenCount`, medido entre 383 y 652) se descuentan del **mismo**
`maxOutputTokens`. Con un techo bajo la respuesta vuelve con
`finishReason: MAX_TOKENS` y el JSON cortado. Los modelos lite razonan 0 tokens.

Por eso el coach ya usa `thinking_budget=0` donde el modelo lo acepta, y
`gemini-3.1-flash-lite` encabeza `COACH_MODELS`: es el único lite actual que no
rechaza ese parámetro con un 400.
