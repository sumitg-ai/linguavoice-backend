# main.py
"""
FastAPI backend with Supabase-backed authentication + usage tracking.

Environment variables required on the backend:
 - OPENAI_API_KEY
 - SUPABASE_URL            (e.g. https://<project-ref>.supabase.co)
 - SUPABASE_ANON_KEY       (anon/public key)
 - SUPABASE_SERVICE_KEY    (service_role key — DO NOT PUT IN FRONTEND)
 - BACKEND_ALLOWED_ORIGINS (optional, comma-separated)
"""
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional
import os
import base64
import traceback
import time
import requests

# ---- Config / env (fail fast) ----
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable on the backend service.")
if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Missing one of SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_KEY environment variables.")

# CORS configuration
allowed = os.getenv("BACKEND_ALLOWED_ORIGINS", "").strip()
if allowed:
    ALLOW_ORIGINS = [o.strip() for o in allowed.split(",") if o.strip()]
else:
    ALLOW_ORIGINS = [
        "https://*.hf.space",
        "https://huggingface.co",
        # you can add your front-end origin here
    ]

app = FastAPI(title="Linguavoice Backend API (Supabase + OpenAI)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---
class TTSRequest(BaseModel):
    text: str
    language: str
    voice: Optional[str] = "alloy"

class MagicLinkRequest(BaseModel):
    email: str
    redirect_to: str

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
        # Server-side call uses service_role as apikey to avoid CORS issues
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
        print("get_app_user_row failed:", r.status_code, r.text)
        return None

def ensure_app_user(user: dict) -> dict:
    user_id = user.get("id")
    email = user.get("email")
    row = get_app_user_row(user_id)
    if row:
        return row
    # create row
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
    if not text:
        return ""
    if target_language.lower() in ("english", "en"):
        return text
    # if you have an OpenAI client configured, you may translate here
    # For now return original text
    return text

# ---- OpenAI TTS util (placeholder — keep your existing implementation) ----
def generate_tts_bytes(translated_text: str, voice: str = "alloy") -> bytes:
    # This must be the same TTS call you used earlier (OpenAI or other).
    # For safety we raise if not implemented. Replace this with your working TTS call.
    raise RuntimeError("TTS implementation missing in this code sample. Insert your existing TTS function here.")

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

@app.post("/auth/send_magic_link")
def send_magic_link(req: MagicLinkRequest):
    """
    Send a Supabase magic link to the provided email.
    redirect_to MUST be registered in Supabase Auth -> Settings -> Redirect URLs.
    """
    email = req.email
    redirect_to = req.redirect_to
    if not email or not redirect_to:
        raise HTTPException(status_code=400, detail="email and redirect_to required")

    # Supabase OTP REST endpoint for magiclink
    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/otp"
    headers = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    body = {"email": email, "type": "magiclink", "redirect_to": redirect_to}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=10)
        if r.status_code in (200, 201, 204):
            return {"status":"ok", "detail":"Magic link sent. Check your email."}
        else:
            raise HTTPException(status_code=500, detail=f"Supabase error: {r.status_code} {r.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate")
async def generate_tts(req: TTSRequest, request: Request, authorization: Optional[str] = Header(None)):
    start_time = time.time()
    try:
        # 1) extract token (optional for anonymous freemium)
        user_token = extract_bearer_token(authorization)
        user = None
        anonymous = False
        if not user_token:
            anonymous = True
        else:
            user = supabase_auth_get_user(user_token)
            if not user or "id" not in user:
                raise HTTPException(status_code=401, detail="Invalid or expired Supabase token.")
            # ensure app user row exists
            ensure_app_user(user)

        # Basic input validation
        if not req.text or not req.language:
            raise HTTPException(status_code=400, detail="text and language are required fields.")

        request_chars = len(req.text or "")
        # anonymous freemium limit = 500 characters
        if anonymous and request_chars > 500:
            raise HTTPException(status_code=402, detail="Anonymous limit is 500 characters. Please login or subscribe.")

        # If logged in, enforce monthly quota from app_users/plans
        if not anonymous:
            app_user = get_app_user_row(user["id"]) or ensure_app_user(user)
            plan = fetch_plan(app_user.get("plan_id") or "free") or {"monthly_quota_chars": 20000}
            monthly_quota = plan.get("monthly_quota_chars", 20000)
            current_usage = app_user.get("usage_monthly") or 0
            if current_usage + request_chars > monthly_quota:
                raise HTTPException(status_code=402, detail="Quota exceeded for this month. Please upgrade your plan.")

        # translate if needed
        translated = translate_text_if_needed(req.text, req.language)

        # generate tts bytes (replace with your existing implementation)
        audio_bytes = generate_tts_bytes(translated, voice=req.voice)

        # convert to base64 for frontend
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        # log usage for authenticated users only
        if not anonymous:
            try:
                log_usage_and_increment(user["id"], request_chars, {"language": req.language, "voice": req.voice})
            except Exception as e:
                print("Warning: failed to log usage:", e)

        elapsed = round(time.time() - start_time, 2)
        return {"status":"success", "translated_text": translated, "audio_base64": audio_base64}

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print("Exception in /generate:", tb)
        return JSONResponse(status_code=500, content={"status":"error", "message": str(e), "traceback": tb.splitlines()[-20:]})

# --- NEW: auth callback page used by Supabase magic link redirect ---
@app.get("/auth_callback", response_class=HTMLResponse)
def auth_callback_page():
    """
    Serve a small HTML page that runs supabase-js to read the session created by the magic link
    and display the access_token (JWT) for the user to copy into the frontend token box.
    The page uses SUPABASE_URL and SUPABASE_ANON_KEY from env vars.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_anon:
        return HTMLResponse("<h3>Supabase config missing on server. Set SUPABASE_URL and SUPABASE_ANON_KEY env vars.</h3>", status_code=500)

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"/><title>Login Successful — Copy Your Token</title></head>
<body style="font-family: Arial; padding:20px;">
  <h2>Login Successful 🎉</h2>
  <p>If you were redirected here after clicking the magic link, this page will extract your session and show the access token (JWT). Copy it and paste it into the app.</p>
  <textarea id="tokenBox" style="width:100%; height:140px;" readonly placeholder="token will appear here..."></textarea>
  <br/><br/>
  <button id="cpy">Copy Token to Clipboard</button>
  <p id="msg" style="color:green"></p>

  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js"></script>
  <script>
  (async () => {{
    const SUPABASE_URL = "{supabase_url}";
    const SUPABASE_ANON_KEY = "{supabase_anon}";
    const supabase = supabasejs.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    const {{ data, error }} = await supabase.auth.getSession();
    if (error) {{
      document.getElementById('tokenBox').value = "Error reading session: " + (error.message || JSON.stringify(error));
      return;
    }}
    const session = data?.session;
    if (!session || !session.access_token) {{
      document.getElementById('tokenBox').value = "No session found. Try reloading or ensure this URL is added to Supabase Redirect URLs.";
      return;
    }}
    const token = session.access_token;
    document.getElementById('tokenBox').value = token;
    document.getElementById('cpy').onclick = async () => {{
      try {{
        await navigator.clipboard.writeText(token);
        document.getElementById('msg').innerText = "Copied! Paste it into the app's token box and click Generate.";
      }} catch (e) {{
        document.getElementById('msg').innerText = "Copy failed; please manually copy the token from the box above.";
      }}
    }};
  }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)

# --- End file ---
