# main.py
"""
Linguavoice backend (FastAPI) with:
 - generate endpoint with translation (OpenAI) + TTS implementation (OpenAI primary, Hugging Face fallback)
 - /auth/send_magic_link (calls Supabase OTP)
 - magic-session endpoints for magic-link auto-fill flow
 - auth_callback page serving client-side supabase-js
 - Stripe checkout session creation + webhook handling to update Supabase app_users
Env required:
 - SUPABASE_URL
 - SUPABASE_ANON_KEY
 - SUPABASE_SERVICE_KEY
 - BACKEND_BASE_URL
 - OPENAI_API_KEY (for TTS + translation) - recommended
 - OPENAI_TTS_MODEL (optional)
 - HF_SPACE_URL (for redirect in callback)
 - HF_SPACE_SECRET (optional fallback)
 - HF_TTS_MODEL (optional)
 - STRIPE_SECRET_KEY (for creating sessions)
 - STRIPE_WEBHOOK_SECRET (for verifying webhooks) - recommended
 - STRIPE_PRICE_ID_PREMIUM (Stripe Price ID for premium plan)
 - STRIPE_PRICE_ID_BASIC (optional)
"""
import os
import time
import uuid
import traceback
import base64
import requests
import stripe
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
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")   # required public backend URL
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "")
HF_SPACE_SECRET = os.getenv("HF_SPACE_SECRET")  # optional fallback
HF_TTS_MODEL = os.getenv("HF_TTS_MODEL", "tts_models/en/ljspeech/tacotron2-DDC")  # change as desired

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")  # recommended
STRIPE_PRICE_ID_PREMIUM = os.getenv("STRIPE_PRICE_ID_PREMIUM")
STRIPE_PRICE_ID_BASIC = os.getenv("STRIPE_PRICE_ID_BASIC")

# Basic validations
if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Please set SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY on backend.")
if not BACKEND_BASE_URL:
    raise RuntimeError("Please set BACKEND_BASE_URL on backend (e.g. https://linguavoice-backend.onrender.com)")

# Configure Stripe if key present
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

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
    language: str    # target language name: "English", "French", "Spanish", "German", "Japanese"
    voice: Optional[str] = "nova"

class MagicLinkRequest(BaseModel):
    email: str
    redirect_to: str

class CheckoutRequest(BaseModel):
    plan: str  # e.g. "premium" or "basic"
    email: Optional[str] = None
    user_id: Optional[str] = None  # optional user id to attach to metadata

# -------- Supabase helpers ----------
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

def update_app_user_by_email(email: str, updates: dict) -> bool:
    """
    Update app_users row by email using Supabase REST API and service role key.
    """
    try:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_users"
        headers = {
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        params = {"email": f"eq.{email}"}
        r = requests.patch(url, headers=headers, params=params, json=updates, timeout=10)
        if r.status_code in (200, 204):
            return True
        print("update_app_user_by_email failed", r.status_code, r.text)
    except Exception as e:
        print("update_app_user_by_email error:", e)
    return False

# ---------- Translation helper (OpenAI chat completion) ----------
_LANGUAGE_MAP = {
    "english": "English",
    "french": "French",
    "spanish": "Spanish",
    "german": "German",
    "japanese": "Japanese",
}

_LANGUAGE_NORMALIZE = {
    "english": "English", "english.": "English",
    "french": "French", "français": "French", "francais": "French",
    "spanish": "Spanish", "español": "Spanish", "espanol": "Spanish",
    "german": "German", "deutsch": "German", "deutsche": "German",
    "japanese": "Japanese", "日本語": "Japanese", "nihongo": "Japanese",
}

def _normalize_language_name(name: str) -> str:
    if not name:
        return ""
    key = name.strip().lower()
    return _LANGUAGE_NORMALIZE.get(key, name.strip())

def detect_language_via_openai(text: str) -> str:
    if not OPENAI_API_KEY:
        return ""
    try:
        system_prompt = (
            "You are a tiny language-detection assistant. Given the user's text, "
            "return only the language name (one word or phrase), e.g. English, French, German, Japanese, Spanish. "
            "Do NOT add any extra text or punctuation."
        )
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.0,
            "max_tokens": 16
        }
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
        if r.status_code == 200:
            j = r.json()
            choices = j.get("choices") or []
            if choices:
                detected = choices[0].get("message", {}).get("content", "").strip()
                normalized = _normalize_language_name(detected)
                return normalized
    except Exception as e:
        print("detect_language_via_openai error:", e)
    return ""

def translate_text_via_openai(text: str, target_language: str) -> str:
    if not OPENAI_API_KEY:
        return text
    target = _LANGUAGE_MAP.get(target_language.strip().lower(), target_language).strip()
    try:
        detected = detect_language_via_openai(text)
        if detected:
            if detected.strip().lower() == target.strip().lower():
                return text
    except Exception as e:
        print("Language detection failed:", e)
    system_prompt = (
        f"You are a translation assistant. Translate the user's text into {target} only. "
        "Do not add commentary or explanations; return only the translated text. Preserve tone and punctuation."
    )
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": 0.0,
        "max_tokens": 2000
    }
    try:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            j = r.json()
            choices = j.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                return content.strip()
            return text
        else:
            print("OpenAI translate failed", r.status_code, r.text[:400])
            return text
    except Exception as e:
        print("translate_text_via_openai error:", e)
        return text

# ---------- TTS implementation ----------
def generate_tts_bytes_openai(text: str, voice: str = "nova") -> Optional[bytes]:
    if not OPENAI_API_KEY:
        return None
    try:
        endpoint = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }
        payload = {
            "model": OPENAI_TTS_MODEL,
            "voice": voice,
            "input": text,
        }
        r = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        if r.status_code == 200 and r.content:
            return r.content
        else:
            print("OpenAI TTS failed", r.status_code, r.text[:500])
            return None
    except Exception as e:
        print("OpenAI TTS error:", e)
        return None

def generate_tts_bytes_hf(text: str, voice: str = "nova") -> Optional[bytes]:
    if not HF_SPACE_SECRET:
        return None
    try:
        hf_model = HF_TTS_MODEL
        endpoint = f"https://api-inference.huggingface.co/models/{hf_model}"
        headers = {"Authorization": f"Bearer {HF_SPACE_SECRET}"}
        payload = {"inputs": text}
        r = requests.post(endpoint, headers=headers, json=payload, timeout=60, stream=True)
        if r.status_code == 200:
            return r.content
        else:
            print("HF TTS failed", r.status_code, r.text[:500])
            return None
    except Exception as e:
        print("HF TTS error:", e)
        return None

def generate_tts_bytes(translated_text: str, voice: str = "nova") -> bytes:
    audio = generate_tts_bytes_openai(translated_text, voice=voice)
    if audio:
        return audio
    audio = generate_tts_bytes_hf(translated_text, voice=voice)
    if audio:
        return audio
    raise RuntimeError("No TTS provider succeeded. Ensure OPENAI_API_KEY and/or HF_SPACE_SECRET + HF_TTS_MODEL are correctly set.")

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
    try:
        # auth handling (unchanged)
        user_token = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
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

        # ---- translation step: translate input into requested target language ----
        translated = req.text
        try:
            translated = translate_text_via_openai(req.text, req.language)
        except Exception as e:
            print("Translation error:", e)
            translated = req.text

        # call TTS (this will try OpenAI then HF)
        audio_bytes = generate_tts_bytes(translated, voice=req.voice)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        return {"status":"success", "translated_text": translated, "audio_base64": audio_b64}
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"status":"error","message": str(e), "traceback": tb.splitlines()[-20:]})

# ---------- Stripe: create checkout session ----------
@app.post("/create-checkout-session")
def create_checkout_session(req: CheckoutRequest):
    """
    Creates a Stripe Checkout session and returns a session URL for the client to open.
    Expects plan param: "premium" (or "basic" if configured).
    Optional email and user_id can be provided; they'll be attached to the Checkout metadata.
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured on server (STRIPE_SECRET_KEY missing).")

    plan = (req.plan or "premium").lower()
    if plan == "premium":
        price_id = STRIPE_PRICE_ID_PREMIUM
    elif plan == "basic":
        price_id = STRIPE_PRICE_ID_BASIC or STRIPE_PRICE_ID_PREMIUM
    else:
        raise HTTPException(status_code=400, detail="Unknown plan requested")

    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe price ID not configured for requested plan")

    try:
        # build success/cancel URLs
        success_url = BACKEND_BASE_URL.rstrip("/") + "/stripe_success?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = BACKEND_BASE_URL.rstrip("/")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=req.email or None,
            metadata={"plan": plan, "user_id": req.user_id or ""},
        )
        return {"url": session.url, "id": session.id}
    except Exception as e:
        print("Stripe create session error:", e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------- Stripe webhook endpoint ----------
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    event = None

    # Verify signature if secret configured
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET)
        except ValueError as e:
            # Invalid payload
            print("Webhook invalid payload", e)
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            print("Webhook signature verification failed", e)
            raise HTTPException(status_code=400, detail="Signature verification failed")
    else:
        # No webhook secret configured - try parsing body directly (less secure)
        try:
            event = stripe.Event.construct_from(request.json(), stripe.api_key)
        except Exception as e:
            print("Webhook construct event failed", e)
            raise HTTPException(status_code=400, detail="Invalid webhook event")

    # Handle the event
    try:
        typ = event.get("type")
        data = event.get("data", {}).get("object", {})

        if typ == "checkout.session.completed":
            session = data
            email = session.get("customer_details", {}).get("email")
            subscription_id = session.get("subscription")
            customer_id = session.get("customer")
            metadata = session.get("metadata", {}) or {}
            plan = metadata.get("plan") or "premium"

            # Update Supabase app_users by email
            if email:
                updates = {
                    "is_subscribed": True,
                    "plan": plan,
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": subscription_id,
                    "stripe_checkout_session_id": session.get("id")
                }
                success = update_app_user_by_email(email, updates)
                if not success:
                    print("Failed to update app_users for", email)
            else:
                print("checkout.session.completed missing customer email; cannot update user row.")
        # Optionally handle subscription.updated, invoice.payment_succeeded etc.
        return {"status": "ok"}
    except Exception as e:
        print("Webhook handler error:", e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------- in-memory magic-session store (unchanged) ----------
_magic_store = {}
_magic_lock = Lock()
_MAGIC_TTL = 300  # seconds

def _cleanup_magic_store():
    now = time.time()
    with _magic_lock:
        expired = [k for k,v in _magic_store.items() if now - v.get("created",0) > _MAGIC_TTL]
        for k in expired:
            _magic_store.pop(k, None)

@app.post("/auth/create_magic_session")
def create_magic_session():
    _cleanup_magic_store()
    key = uuid.uuid4().hex[:16]
    with _magic_lock:
        _magic_store[key] = {"token": None, "created": time.time()}
    redirect_to = BACKEND_BASE_URL.rstrip("/") + f"/auth_callback?key={key}"
    return {"key": key, "redirect_to": redirect_to}

@app.post("/auth/receive_token")
def receive_token(payload: dict):
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
    _cleanup_magic_store()
    with _magic_lock:
        entry = _magic_store.get(key)
        if not entry:
            raise HTTPException(status_code=404, detail="session key not found or expired")
        token = entry.get("token")
        if not token:
            return JSONResponse(status_code=204, content={})
        _magic_store.pop(key, None)
        return {"token": token}

# ---------- auth_callback page (serves HTML) ----------
@app.get("/auth_callback", response_class=HTMLResponse)
def auth_callback_page(request: Request):
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
