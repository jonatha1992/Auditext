"""One-shot Live + coach connectivity check. Does not print secrets."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from google import genai
from google.genai import types

from infrastructure.services import gemini_keys, interview_live


async def try_connect() -> bool:
    gemini_keys.pool.reload()
    print("keys_configured=", gemini_keys.is_configured(), "count=", gemini_keys.pool.count())
    sample = interview_live.parse_assist_text(
        '{"pregunta_es":"hola","respuestas":["hi","hello"]}'
    )
    print("unit_parse=", None if sample is None else sample.pregunta_es)

    key = gemini_keys.pool.current()
    if not key:
        print("LIVE_CONNECT=FAIL no_key")
        return False

    # Coach path (generate_content)
    try:
        assist = interview_live.coach_assist(
            "Backend Python developer, 5 years Django",
            "Tell me about your experience with Django and APIs.",
            key,
        )
        print(
            "COACH_OK=",
            assist is not None,
            "pregunta=",
            (assist.pregunta_es[:60] if assist else None),
            "n_replies=",
            (len(assist.respuestas) if assist else 0),
        )
    except Exception as e:
        print("COACH_FAIL=", type(e).__name__, str(e)[:200])

    models = [m for m in interview_live.LIVE_MODELS if m]
    print("models_try=", models)
    # Build the config the app actually uses. A hand-rolled copy here would keep
    # passing while the real one is rejected — which is exactly the failure this
    # script exists to catch, now that the config carries session_resumption and
    # context_window_compression.
    probe = interview_live.InterviewLiveSession(
        context="smoke",
        on_transcript=lambda _t: None,
        on_assist=lambda _a: None,
        on_status=lambda _s: None,
        mode="examen_oral",
    )
    last = None
    for model in models:
        try:
            client = genai.Client(api_key=key)
            config = probe._live_config(types)
            async with client.aio.live.connect(model=model, config=config) as session:
                import numpy as np

                silence = np.zeros(8000, dtype=np.int16).tobytes()
                await session.send_realtime_input(
                    audio=types.Blob(data=silence, mime_type="audio/pcm;rate=16000")
                )
                print("LIVE_CONNECT=OK model=", model)
                return True
        except Exception as e:
            last = e
            print(
                "LIVE_FAIL model=",
                model,
                "err=",
                type(e).__name__,
                str(e)[:240],
            )
    print(
        "LIVE_CONNECT=FAIL last=",
        type(last).__name__ if last else None,
        str(last)[:300] if last else None,
    )
    return False


if __name__ == "__main__":
    ok = asyncio.run(try_connect())
    raise SystemExit(0 if ok else 2)
