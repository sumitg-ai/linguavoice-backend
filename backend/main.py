# main.py
"""
Linguavoice backend (FastAPI) with:
 - existing /generate skeleton (keeps your quota logic)
 - /auth/send_magic_link (calls Supabase OTP)
 - new magic-session endpoints:
      POST /auth/create_magic_session -> returns {key, redirect_to}
      POST /auth/receive_token        -> auth_callback posts {key, token}
      GET  /auth/poll_token?key=...  -> HF Space polls for token
 - GET /auth_callback serves small HTML page that reads Supabase session and posts token to /auth/receive_token
ENV VARS required on backend (Render):
 - SUPABASE_URL
 - SUPABASE_ANON_KEY
 - SUPABASE_SERVICE_KEY
 - OPENAI_API_KEY
 - BACKEND_BASE_URL   (e.g. https://linguavoice-backend.onrender.com)
 - HF_SPACE_URL       (e.g. https://sumitg1979-international-multilingual-tts.hf.space) - optional but recommended
"""
import os
import time
import uuid
import traceback
import base64
import requests
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

# -------- env & sanity checks --------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")   # required
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Please set SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY on backend.")
if not OPENAI_API_KEY:
    raise RuntimeError("Please set OPENAI_API_KEY on backend.")
if not BACKEND_BASE_URL:
    raise RuntimeError("Please set BACKEND_BASE_URL on backend (e.g. https://linguavoice-backend.onrender.com)")

# CORS - allow HF Space + common domains (add more as needed)
ALLOW_ORIGINS = [
    HF_SPACE_URL,
    "https://*.hf.space",
    "https://huggingface.co"
]
ALLOW_ORIGINS = [o for o in ALLOW_ORIGINS if o]

app = FastAPI(title="Linguavoice Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# -------- models ----------
class TTSRequest(BaseModel):
    text: str
    language: str
    voice: Optional[str] = "nova"

class MagicLinkRequest(BaseModel):
    email: str
    redirect_to: str

# -------- Supabase helpers (unchanged logic from your original file) ----------
def supabase_auth_get_user(access_token: str) -> Optional[dict]:
    if not access_token:
        return None
    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/user"
    headers = {"Authorization": f"Bearer {access_token}", "apikey": SUPABASE_SERVICE_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("supabase_auth_get_user error:", e)
    return None

def get_app_user_row(user_id: str) -> Optional[dict]:
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Accept": "application/json"}
    params = {"id": f"eq.{user_id}", "select": "*"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code in (200, 206):
            items = r.json()
            return items[0] if items else None
    except Exception as e:
        print("get_app_user_row error:", e)
    return None

def ensure_app_user(user: dict) -> dict:
    user_id = user.get("id")
    email = user.get("email")
    row = get_app_user_row(user_id)
    if row:
        return row
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}", "Content-Type":"application/json", "Prefer":"return=representation"}
    payload = {"id": user_id, "email": email}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    if r.status_code in (200,201):
        items = r.json()
        return items[0] if isinstance(items, list) else items
    raise RuntimeError("Failed to ensure app_user row")

# ---------- placeholder TTS function (use your implementation) ----------
def generate_tts_bytes(translated_text: str, voice: str = "nova") -> bytes:
    # Replace with your working TTS implementation. This placeholder raises.
    raise RuntimeError("TTS implementation missing here. Replace with your existing TTS call.")

# ---------- endpoints (health/generate/send magic link) ----------
@app.get("/health")
def health_check():
    return {"status":"ok"}

@app.post("/auth/send_magic_link")
def send_magic_link(req: MagicLinkRequest):
    email = req.email
    redirect_to = req.redirect_to
    if not email or not redirect_to:
        raise HTTPException(status_code=400, detail="email and redirect_to required")
    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/otp"
    headers = {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}
    body = {"email": email, "type": "magiclink", "redirect_to": redirect_to}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=10)
        if r.status_code in (200,201,204):
            return {"status":"ok", "detail":"Magic link sent"}
        else:
            raise HTTPException(status_code=500, detail=f"Supabase error: {r.status_code} {r.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate")
def generate_tts(req: TTSRequest, authorization: Optional[str] = Header(None)):
    # keep your existing generate logic (quota checks, translation, TTS call). This is a simplified wrapper:
    try:
        user_token = None
        if authorization:
            parts = authorization.split()
            if len(parts)==2 and parts[0].lower()=="bearer":
                user_token = parts[1]
        user = None
        anonymous = False
        if not user_token:
            anonymous = True
        else:
            user = supabase_auth_get_user(user_token)
            if not user or "id" not in user:
                raise HTTPException(status_code=401, detail="Invalid token")
            ensure_app_user(user)

        if not req.text or not req.language:
            raise HTTPException(status_code=400, detail="text and language required")

        chars = len(req.text)
        if anonymous and chars > 500:
            raise HTTPException(status_code=402, detail="Anonymous users limited to 500 chars. Please login/subscribe.")

        translated = req.text  # translation logic placeholder

        audio_bytes = generate_tts_bytes(translated, voice=req.voice)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        # log usage, update app_user usage etc (your existing implementation can be re-used)

        return {"status":"success", "translated_text": translated, "audio_base64": audio_b64}
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"status":"error","message": str(e), "traceback": tb.splitlines()[-10:]})

# ---------- in-memory magic-session store ----------
_magic_store = {}   # key -> {"token": Optional[str], "created": float}
_magic_lock = Lock()
_MAGIC_TTL = 300  # seconds (5 minutes)

def _cleanup_magic_store():
    now = time.time()
    with _magic_lock:
        expired = [k for k,v in _magic_store.items() if now - v.get("created",0) > _MAGIC_TTL]
        for k in expired:
            _magic_store.pop(k, None)

@app.post("/auth/create_magic_session")
def create_magic_session():
    """
    Creates a short-lived key and returns redirect url for Supabase (contains ?key=...).
    Frontend will use redirect_to returned here when calling /auth/send_magic_link.
    """
    _cleanup_magic_store()
    key = uuid.uuid4().hex[:16]
    with _magic_lock:
        _magic_store[key] = {"token": None, "created": time.time()}
    redirect_to = BACKEND_BASE_URL.rstrip("/") + f"/auth_callback?key={key}"
    return {"key": key, "redirect_to": redirect_to}

@app.post("/auth/receive_token")
def receive_token(payload: dict):
    """
    POSTed by auth_callback page: {key, token}
    """
    key = payload.get("key")
    token = payload.get("token")
    if not key or not token:
        raise HTTPException(status_code=400, detail="key and token required")
    _cleanup_magic_store()
    with _magic_lock:
        if key not in _magic_store:
            raise HTTPException(status_code=404, detail="session key not found or expired")
        _magic_store[key]["token"] = token
    return {"status":"ok"}

@app.get("/auth/poll_token")
def poll_token(key: str):
    """
    Polled by HF Space frontend. 204 if token not available yet, 200 + {token} when ready.
    """
    _cleanup_magic_store()
    with _magic_lock:
        entry = _magic_store.get(key)
        if not entry:
            raise HTTPException(status_code=404, detail="session key not found or expired")
        token = entry.get("token")
        if not token:
            return JSONResponse(status_code=204, content={})
        # one-time: remove mapping so token cannot be polled again
        _magic_store.pop(key, None)
        return {"token": token}

# ---------- auth_callback page (serves HTML) ----------
@app.get("/auth_callback", response_class=HTMLResponse)
def auth_callback_page(request: Request):
    """
    Serves a small HTML page (client will read Supabase session via supabase-js)
    and POST { key, token } to backend /auth/receive_token.
    The page gets SUPABASE_URL and SUPABASE_ANON_KEY from server env and BACKEND_BASE_URL, HF_SPACE_URL.
    """
    supabase_url = SUPABASE_URL or ""
    supabase_anon = SUPABASE_ANON_KEY or ""
    backend_base = BACKEND_BASE_URL or ""
    app_home = HF_SPACE_URL or ""

    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"/><title>Login Successful — Copy Token</title></head>
<body style="font-family:Arial;padding:18px;">
  <h2>Login Successful 🎉</h2>
  <p>This page extracts the Supabase session (created by the magic link) and posts the access token to the backend so your app can auto-fill it.</p>
  <textarea id="tokenBox" style="width:100%;height:140px;" readonly placeholder="token will appear here..."></textarea><br/><br/>
  <button id="cpy">Copy Token</button>
  <p id="msg" style="color:green"></p>
  <p id="return"></p>

  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js"></script>
  <script>
  (async () => {{
    const SUPABASE_URL = "{supabase_url}";
    const SUPABASE_ANON_KEY = "{supabase_anon}";
    const BACKEND_BASE = "{backend_base}";
    const APP_HOME = "{app_home}";
    const supabase = supabasejs.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

    const urlParams = new URLSearchParams(window.location.search);
    const key = urlParams.get('key');

    const {{ data, error }} = await supabase.auth.getSession();
    if (error) {{
      document.getElementById('tokenBox').value = "Error reading session: " + (error.message || JSON.stringify(error));
      return;
    }}
    const session = data?.session;
    if (!session || !session.access_token) {{
      document.getElementById('tokenBox').value = "No session found. Try reloading or ensure this URL is in Supabase Redirect URLs.";
      return;
    }}
    const token = session.access_token;
    document.getElementById('tokenBox').value = token;
    document.getElementById('cpy').onclick = async () => {{
      try {{
        await navigator.clipboard.writeText(token);
        document.getElementById('msg').innerText = "Copied! Paste into the app if needed.";
      }} catch (e) {{
        document.getElementById('msg').innerText = "Copy failed; please copy manually.";
      }}
    }};

    if (!key) {{
      document.getElementById('return').innerText = "No key provided — retry from the app.";
      return;
    }}

    try {{
      const res = await fetch(BACKEND_BASE + '/auth/receive_token', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ key: key, token: token }})
      }});
      if (res.ok) {{
        document.getElementById('msg').innerText = "Token posted to server. Return to the app; it will auto-fill shortly.";
        if (APP_HOME) {{
          document.getElementById('return').innerHTML = `<a href="${{APP_HOME}}" target="_blank">Return to App</a>`;
        }}
      }} else {{
        const text = await res.text();
        document.getElementById('msg').innerText = "Server POST failed: " + res.status + " " + text;
      }}
    }} catch (e) {{
      document.getElementById('msg').innerText = "Failed to post token to server: " + e;
    }}
  }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)
