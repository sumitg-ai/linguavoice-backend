# backend/main.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import os
import base64
import traceback
import time

# ---- Config ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable on the backend service.")

# Allowed origins for CORS (comma-separated env var)
allowed = os.getenv("BACKEND_ALLOWED_ORIGINS", "").strip()
if allowed:
    ALLOW_ORIGINS = [o.strip() for o in allowed.split(",") if o.strip()]
else:
    # Default: allow HF spaces for now (secure it later)
    ALLOW_ORIGINS = [
        "https://*.hf.space",
        "https://huggingface.co",
        "https://sumitg1979--international-multilingual-tts.hf.space",
        "*"
    ]

# ---- FastAPI app ----
app = FastAPI(title="Linguavoice Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- OpenAI client ----
client = OpenAI(api_key=OPENAI_API_KEY)

# ---- Request model ----
class TTSRequest(BaseModel):
    text: str
    language: str
    voice: str = "alloy"


# ---- Utilities ----
def translate_text_if_needed(text: str, target_language: str) -> str:
    """Return translated text (or original if English). Uses chat completion model."""
    if not text:
        return ""
    if target_language.lower() in ("english", "en"):
        return text

    try:
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a translator that converts any input text to {target_language}. "
                        "Return only the translated text and no other commentary."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=1500,
        )
        # robust extraction depending on SDK shape
        translated = None
        if isinstance(resp, dict):
            # openai python client may return dict-like structure
            choices = resp.get("choices", [])
            if choices:
                # new SDK: message -> content
                translated = choices[0].get("message", {}).get("content")
        else:
            # older/other client shapes
            try:
                translated = resp.choices[0].message.content
            except Exception:
                translated = str(resp)

        if translated is None:
            return text  # fallback: return original if translation unavailable

        return translated.strip()
    except Exception as e:
        raise RuntimeError(f"Translation failed: {e}")


# ---- Endpoints ----
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Linguavoice Backend"}


@app.post("/generate")
async def generate_tts(req: TTSRequest, request: Request):
    """
    Accepts JSON {text, language, voice}
    Returns JSON:
      { "status":"success", "translated_text":"...", "audio_base64":"<base64 mp3>" }
    On error: logs full traceback and returns structured JSON error.
    """
    start_time = time.time()
    print("\n=== /generate called ===")
    try:
        print("Request payload snippet:", req.dict())

        if not req.text or not req.language:
            raise HTTPException(status_code=400, detail="text and language are required fields.")

        # 1) translate if needed
        translated = translate_text_if_needed(req.text, req.language)
        print(f"Translated text snippet: {translated[:200]}")

        # 2) call OpenAI TTS
        # Request a real audio format (mp3) and convert bytes -> base64 for the frontend.
        tts_resp = client.audio.speech.create(
            model="tts-1",
            voice=req.voice,
            input=translated,
            response_format="mp3",  # request mp3 bytes back
        )

        # 3) Extract raw bytes from the response in a few possible shapes
        audio_bytes = None

        # Some SDK versions put bytes in .content
        if hasattr(tts_resp, "content") and tts_resp.content:
            audio_bytes = tts_resp.content

        # Some shapes return bytes-like object when cast to bytes
        if not audio_bytes:
            try:
                audio_bytes = bytes(tts_resp)
            except Exception:
                audio_bytes = None

        # Some older/different shapes return a dict with a known key
        if not audio_bytes and isinstance(tts_resp, dict):
            # possible keys: 'audio', 'data' -> {'audio': b'...'} etc.
            audio_bytes = tts_resp.get("audio") or tts_resp.get("data", {}).get("audio") or tts_resp.get("content")

        if not audio_bytes:
            raise RuntimeError("TTS succeeded but no audio bytes were returned in an expected location.")

        # Ensure bytes type (if it's str containing base64, handle that)
        if isinstance(audio_bytes, str):
            # If SDK already returned base64 string, pass it through
            try:
                # detect if looks like base64 (heuristic)
                base64.b64decode(audio_bytes, validate=True)
                audio_base64 = audio_bytes
            except Exception:
                # if it's text but not base64, encode it
                audio_base64 = base64.b64encode(audio_bytes.encode("utf-8")).decode("utf-8")
        else:
            # bytes -> base64 string
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        elapsed = round(time.time() - start_time, 2)
        print(f"TTS generation completed successfully in {elapsed}s")
        print("=== /generate end ===\n")

        return {
            "status": "success",
            "translated_text": translated,
            "audio_base64": audio_base64,
        }

    except HTTPException as e:
        print("HTTPException:", e.detail)
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print("=== Exception in /generate endpoint ===")
        print(tb)
        print("=== End Exception ===\n")

        error_payload = {
            "status": "error",
            "error": "internal_server_error",
            "message": str(e),
            "traceback": tb.splitlines()[-10:],  # last few lines for easier debugging
        }
        return JSONResponse(status_code=500, content=error_payload)
