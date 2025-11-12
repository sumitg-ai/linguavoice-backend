# main.py
"""
FastAPI backend with Supabase-backed authentication + usage tracking.

Endpoints:
 - GET  /health
 - POST /generate    (protected — requires Supabase access token)
 - GET  /me          (protected — returns user + plan + usage)
 - GET  /usage       (protected — returns recent usage logs for the user)

Environment variables required:
 - OPENAI_API_KEY
 - SUPABASE_URL            (e.g. https://<project-ref>.supabase.co)
 - SUPABASE_ANON_KEY       (anon/public key)
 - SUPABASE_SERVICE_KEY    (service_role key — DO NOT PUT IN FRONTEND)
 - BACKEND_ALLOWED_ORIGINS (optional, comma-separated)
"""
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
import base64
import traceback
import time
import requests
from typing import Optional

# ---- Config / env (fail fast) ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable on the backend service.")
if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Missing one of SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_KEY environment variables.")

# CORS
allowed = os.getenv("BACKEND_ALLOWED_ORIGINS", "").strip()
if allowed:
    ALLOW_ORIGINS = [o.strip() for o in allowed.split(",") if o.strip()]
else:
    ALLOW_ORIGINS = [
        "https://*.hf.space",
        "https://huggingface.co",
        # add your exact HF space URL if desired
    ]

# ---- App ----
app = FastAPI(title="Linguavoice Backend API (Supabase + OpenAI)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Optional: import OpenAI SDK client (keeps your previous behavior) ----
# You were previously using `openai` SDK or the new OpenAI client. Keep using the same pattern.
# Example with new openai package-style client (adapt if you use a different SDK).
try:
    # If you use the official "openai" package:
    import openai
    openai.api_key = OPENAI_API_KEY
    OPENAI_CLIENT = "openai_sdk"
except Exception:
    OPENAI_CLIENT = None

# --- Models ---
class TTSRequest(BaseModel):
    text: str
    language: str
    voice: Optional[str] = "alloy"


# ---- Supabase helpers ----
def supabase_auth_get_user(access_token: str) -> Optional[dict]:
    """
    Verify a Supabase access token by calling /auth/v1/user
    Returns user dict (includes 'id', 'email') or None.
    """
    if not access_token:
        return None
    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/user"
    headers = {
        "Authorization": f"Bearer {access_token}",
        # Use service key as apikey so Supabase accepts the call server-side
        "apikey": SUPABASE_SERVICE_KEY,
    }
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return None
    return None


def get_app_user_row(user_id: str) -> Optional[dict]:
    """GET /rest/v1/app_users?id=eq.<user_id>"""
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept": "application/json",
    }
    params = {"id": f"eq.{user_id}", "select": "*"}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code in (200, 206):
        items = r.json()
        if items:
            return items[0]
        return None
    else:
        # log
        print("get_app_user_row failed:", r.status_code, r.text)
        return None


def ensure_app_user(user: dict) -> dict:
    """
    Ensure a row exists in app_users for this user id.
    Returns the app_user row.
    """
    user_id = user.get("id")
    email = user.get("email")
    row = get_app_user_row(user_id)
    if row:
        return row

    # create
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    payload = {"id": user_id, "email": email}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    if r.status_code in (201, 200):
        items = r.json()
        if isinstance(items, list):
            return items[0]
        return items
    else:
        # If create fails due to FK (auth.users may not be visible) or RLS, log and raise
        print("ensure_app_user create failed:", r.status_code, r.text)
        raise RuntimeError("Failed to ensure app_user row for user: " + str(r.text))


def fetch_plan(plan_id: str) -> Optional[dict]:
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/plans"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    params = {"id": f"eq.{plan_id}", "select": "*"}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code == 200:
        items = r.json()
        return items[0] if items else None
    print("fetch_plan failed:", r.status_code, r.text)
    return None


def log_usage_and_increment(user_id: str, chars_used: int, request_meta: dict):
    """
    Insert into usage_logs and increment app_users.usage_monthly by chars_used.
    """
    # 1) insert usage_logs
    url_logs = f"{SUPABASE_URL.rstrip('/')}/rest/v1/usage_logs"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    payload = {"user_id": user_id, "chars_used": chars_used, "request_meta": request_meta}
    r = requests.post(url_logs, headers=headers, json=payload, timeout=10)
    if r.status_code not in (201, 200):
        print("Failed to insert usage_log:", r.status_code, r.text)

    # 2) increment usage_monthly
    # Supabase allows PATCH with arithmetic via `Prefer: return=representation` is not atomic for arithmetic,
    # but we will fetch current and update
    user_row = get_app_user_row(user_id)
    if not user_row:
        print("log_usage_and_increment: app_user not found when updating usage_monthly")
        return

    new_usage = (user_row.get("usage_monthly") or 0) + chars_used
    url_user = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
    headers_user = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    params = {"id": f"eq.{user_id}"}
    payload_user = {"usage_monthly": new_usage}
    r2 = requests.patch(url_user, headers=headers_user, params=params, json=payload_user, timeout=10)
    if r2.status_code not in (200, 204):
        print("Failed to update app_user usage_monthly:", r2.status_code, r2.text)


# ---- Translation util (keeps your previous behavior) ----
def translate_text_if_needed(text: str, target_language: str) -> str:
    """Return translated text (or original if English). Uses OpenAI chat completion if available."""
    if not text:
        return ""
    if target_language.lower() in ("english", "en"):
        return text

    try:
        # Use basic OpenAI chat completion via 'openai' package if available.
        if OPENAI_CLIENT == "openai_sdk":
            # This uses the classic OpenAI python package pattern
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a translator that converts any input text to {target_language}. Return only the translated text."
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=1500,
            )
            # extract
            translated = resp.choices[0].message["content"]
            return translated.strip()
        else:
            # Fallback: return original text (you can add another client)
            return text
    except Exception as e:
        raise RuntimeError(f"Translation failed: {e}")


# ---- OpenAI TTS util (keeps your previous logic) ----
def generate_tts_bytes(translated_text: str, voice: str = "alloy") -> bytes:
    """
    Call OpenAI TTS. Returns raw bytes (mp3).
    Adapt this function if your OpenAI client API shape differs.
    """
    # If you use the modern OpenAI API, you may need to call the SDK method you used before.
    # The original code used `client.audio.speech.create(model="tts-1", ...)`.
    # Here we'll attempt to call via the 'openai' package if present.
    if OPENAI_CLIENT == "openai_sdk":
        # Using openai.Audio.speechs? The exact method depends on SDK version.
        # If your existing code used client.audio.speech.create, keep that instead.
        # We'll try to call `openai.audio.speech.create` if available.
        try:
            # try the same shape as before (may need adjustment to match SDK)
            tts_resp = openai.Audio.speech.create(
                model="tts-1",
                voice=voice,
                input=translated_text,
                response_format="mp3",
            )
            # Try a few shapes to extract bytes
            if hasattr(tts_resp, "content") and tts_resp.content:
                return tts_resp.content
            try:
                return bytes(tts_resp)
            except Exception:
                pass
            if isinstance(tts_resp, dict):
                audio = tts_resp.get("audio") or tts_resp.get("data", {}).get("audio") or tts_resp.get("content")
                if isinstance(audio, str):
                    # base64 string
                    return base64.b64decode(audio)
                elif isinstance(audio, (bytes, bytearray)):
                    return bytes(audio)
            raise RuntimeError("TTS returned unexpected response shape.")
        except Exception as e:
            # bubble up for outer handler
            raise
    else:
        raise RuntimeError("No OpenAI client configured for TTS. Install and configure openai SDK.")


# ---- Endpoints ----
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Linguavoice Backend (Supabase+OpenAI)"}


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


@app.post("/generate")
async def generate_tts(req: TTSRequest, request: Request, authorization: Optional[str] = Header(None)):
    start_time = time.time()
    print("\n=== /generate called ===")
    try:
        print("Request payload snippet:", req.dict())

        # 1) protect endpoint: extract and verify Supabase token
        user_token = extract_bearer_token(authorization)
        if not user_token:
            raise HTTPException(status_code=401, detail="Missing Authorization Bearer token.")

        user = supabase_auth_get_user(user_token)
        if not user or "id" not in user:
            raise HTTPException(status_code=401, detail="Invalid or expired Supabase token.")

        # ensure app user exists
        app_user = ensure_app_user(user)

        # Basic input validation
        if not req.text or not req.language:
            raise HTTPException(status_code=400, detail="text and language are required fields.")

        # Plan/quota enforcement
        plan_id = app_user.get("plan_id") or "free"
        plan = fetch_plan(plan_id) or {"monthly_quota_chars": 20000}
        monthly_quota = plan.get("monthly_quota_chars", 20000)
        current_usage = app_user.get("usage_monthly") or 0
        request_chars = len(req.text or "")
        if current_usage + request_chars > monthly_quota:
            raise HTTPException(status_code=402, detail="Quota exceeded for this month. Please upgrade your plan.")

        # 2) translate if needed
        translated = translate_text_if_needed(req.text, req.language)
        print(f"Translated text snippet: {translated[:200]}")

        # 3) generate TTS bytes
        audio_bytes = generate_tts_bytes(translated, voice=req.voice)

        # 4) convert to base64 for frontend
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        # 5) log usage and increment
        request_meta = {"language": req.language, "voice": req.voice, "chars": request_chars, "email": user.get("email")}
        try:
            log_usage_and_increment(user["id"], request_chars, request_meta)
        except Exception as e:
            # log but don't fail the TTS response
            print("Warning: failed to log usage:", e)

        elapsed = round(time.time() - start_time, 2)
        print(f"TTS generation completed successfully in {elapsed}s")
        print("=== /generate end ===\n")

        return {
            "status": "success",
            "translated_text": translated,
            "audio_base64": audio_base64,
        }

    except HTTPException:
        # re-raise so FastAPI handles properly
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
            "traceback": tb.splitlines()[-20:],  # last lines
        }
        return JSONResponse(status_code=500, content=error_payload)


@app.get("/me")
def me(authorization: Optional[str] = Header(None)):
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token.")

    user = supabase_auth_get_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Supabase token.")

    app_user = ensure_app_user(user)
    plan = fetch_plan(app_user.get("plan_id") or "free")
    return {"user": user, "app_user": app_user, "plan": plan}


@app.get("/usage")
def usage(authorization: Optional[str] = Header(None), limit: int = 20):
    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token.")
    user = supabase_auth_get_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Supabase token.")

    user_id = user.get("id")
    # fetch last 'limit' usage logs
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/usage_logs"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    params = {"user_id": f"eq.{user_id}", "select": "*", "order": "created_at.desc", "limit": limit}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code == 200:
        return {"usage_logs": r.json()}
    else:
        print("usage fetch failed:", r.status_code, r.text)
        raise HTTPException(status_code=500, detail="Failed to fetch usage logs.")


# --- End file ---
