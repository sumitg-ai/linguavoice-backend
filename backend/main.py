# backend/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
import os
import base64

# ---- Config ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable on the backend service.")

# Allowed origins for CORS (comma-separated env var). Example:
# BACKEND_ALLOWED_ORIGINS="https://my-space.hf.space,https://*.hf.space"
allowed = os.getenv("BACKEND_ALLOWED_ORIGINS", "").strip()
if allowed:
    ALLOW_ORIGINS = [o.strip() for o in allowed.split(",") if o.strip()]
else:
    # default permissive for initial testing (change to explicit HF space URL in production)
    ALLOW_ORIGINS = ["*"]

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
        # Using chat completions to perform translation reliably
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
        translated = resp.choices[0].message.content.strip()
        return translated
    except Exception as e:
        # If translation fails, return original text and log the reason in an error field later
        raise RuntimeError(f"Translation failed: {e}")

# ---- Endpoints ----
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Linguavoice Backend"}

@app.post("/generate")
def generate_tts(req: TTSRequest):
    """
    Accepts JSON {text, language, voice}
    Returns JSON:
      { "status":"success", "translated_text":"...", "audio_base64":"<base64 mp3>" }
    On error: raises HTTPException
    """
    if not req.text or not req.language:
        raise HTTPException(status_code=400, detail="text and language are required fields.")

    try:
        # 1) translate if needed
        translated = translate_text_if_needed(req.text, req.language)

        # 2) call OpenAI TTS - ask for base64 encoded response
        # NOTE: model name may change; use the TTS model you have access to.
        tts_resp = client.audio.speech.create(
            model="tts-1",           # adjust if your org uses different model name
            voice=req.voice,
            input=translated,
            response_format="base64",
        )

        # Depending on SDK shape: try common locations for base64 payload
        audio_b64 = None
        if isinstance(tts_resp, dict):
            # some SDKs return {"audio": "<base64>"} or {"data": {"audio": "..."}}
            audio_b64 = tts_resp.get("audio") or tts_resp.get("audio_base64") or tts_resp.get("data", {}).get("audio")
        else:
            # try attribute
            audio_b64 = getattr(tts_resp, "audio", None) or getattr(tts_resp, "content", None)

        if not audio_b64:
            # As fallback: try to convert response to bytes then base64 encode
            try:
                raw = bytes(tts_resp)
                audio_b64 = base64.b64encode(raw).decode("utf-8")
            except Exception:
                raise RuntimeError("TTS succeeded but no audio was returned in expected format.")

        return {
            "status": "success",
            "translated_text": translated,
            "audio_base64": audio_b64
        }

    except RuntimeError as rte:
        raise HTTPException(status_code=500, detail=str(rte))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")
