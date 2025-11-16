🚀 Linguavoice AI — Multilingual Text-to-Speech (International Edition)

A fully production-ready multilingual TTS application with automatic translation, magic-link authentication, and seamless frontend/backend integration.

🌍 Overview

Linguavoice AI is a multilingual Text-to-Speech (TTS) platform enabling users to:

Input text in any language

Translate it automatically into the selected target language

Generate high-quality spoken audio using OpenAI TTS with HuggingFace fallback

Use a magic-link login system powered by Supabase

Support freemium usage (anonymous 500-char limit)

The app is composed of:

🖥️ Frontend (HuggingFace Spaces)

Built using Gradio

Provides UI for text input, language selection, voice options, login, and audio playback

Polls backend for magic-link login token

Sends generation requests to backend

🟦 Backend (FastAPI on Render)

Handles TTS generation

Performs OpenAI-powered translation

Implements magic-link login with Supabase

Stores one-time login tokens in memory

Serves secure auth_callback HTML integrating Supabase JavaScript SDK

🗂️ Project Structure
.
├── app.py               # Frontend (Gradio) — Lives in HuggingFace Space
├── main.py              # Backend (FastAPI) — Hosted on Render
├── requirements.txt     # Frontend dependencies
└── README.md

🔧 Frontend (app.py)

The frontend is deployed on HuggingFace Spaces and provides:

⭐ Key Features

Text input box (“Enter text any language”)

Translation & multilingual support (English, French, German, Spanish, Japanese)

Voice styles (OpenAI voices like “nova”)

Magic-link login UI:

Email entry

“Send Magic Link” button

Auto-filled JWT token

Audio preview player

MP3 download link

Call to backend /generate (includes JWT auth if logged-in)

⭐ Magic Link Flow in Frontend

User enters email → clicks Send Magic Link

Frontend calls backend /auth/create_magic_session

Backend returns { key, redirect_to }

Frontend POSTs to /auth/send_magic_link

Frontend polls /auth/poll_token?key=XXXX

As soon as backend receives token → frontend auto-fills login token

⭐ Environment Variables (HuggingFace)

Set these under Spaces → Settings → Secrets:

Name	Purpose
BACKEND_URL	Full backend URL on Render (e.g. https://linguavoice-backend.onrender.com)

Example frontend call:

r = requests.post(f"{BACKEND_URL}/generate", headers={"Authorization": f"Bearer {token}"}, json=data)

🟦 Backend (main.py)

Backend is deployed on Render.com and provides:

/generate → TTS translation + audio generation

/auth/send_magic_link → Sends Supabase OTP magic link

/auth/create_magic_session → Creates one-time login session

/auth/receive_token → Stores access token for frontend auto-fill

/auth/poll_token → Returns token when login completes

/auth_callback → Secure Supabase JS page that extracts session token and posts to backend

⭐ Backend Features
🔠 Automatic Translation

Before generating speech, backend:

Detects source language

Normalizes language names

If source == target → skip translation

Else translates via OpenAI GPT-3.5-Turbo

🔊 TTS Generation

Primary: OpenAI TTS (“gpt-4o-mini-tts”)
Fallback: HuggingFace Inference API

🔐 Magic Link Authentication

Supabase email magic-link auth:

Flow:

Backend calls Supabase /auth/v1/otp

User clicks magic link

Supabase redirects to backend /auth_callback?key=...

Backend extracts JWT → stores token under session key

Frontend polls until token is available → login succeeded

⚙️ Backend Environment Variables (Render.com)
Variable	Needed For	Example
SUPABASE_URL	Supabase Auth	https://xxxx.supabase.co
SUPABASE_ANON_KEY	Client auth	(public key)
SUPABASE_SERVICE_KEY	Backend auth	(service role key)
BACKEND_BASE_URL	Redirect Handler	https://linguavoice-backend.onrender.com
OPENAI_API_KEY	Translation + TTS	Secret
OPENAI_TTS_MODEL	TTS	gpt-4o-mini-tts
HF_SPACE_SECRET	HF fallback TTS	optional
HF_SPACE_URL	Returning to HF UI	your Space URL
🔒 Supabase Configuration
Auth → URL Configuration
Setting	Value
Site URL	HF Space URL (https://<space>.hf.space)
Redirect URLs	https://linguavoice-backend.onrender.com/auth_callback
Email Templates

Works with default templates.

▶️ How to Use
1. Enter Your Text

Any language is accepted (English, French, Spanish, German, Japanese, etc.)

2. Choose Output Language

Backend auto-translates and speaks in that language.

3. Optional: Log In

Enter email → receive magic link → click
You return to the HF UI, fully authenticated.

Free users: 500 characters max
Logged-in users: higher limits (expandable for subscriptions)

4. Click “Generate Speech”

The translated text appears

Audio preview + MP3 download becomes available

📦 Installation (Local Development)
Backend
pip install fastapi uvicorn requests python-multipart
uvicorn main:app --reload

Frontend (Gradio)
pip install gradio requests
python app.py

🧪 Testing the Magic Link Flow

Click Send Magic Link

Check your inbox

Click link → redirected to /auth_callback

Token posted to backend

HF UI auto-fills token and logs you in

💡 Future Enhancements

Stripe subscription tiers

Usage quotas stored in Supabase

Analytics dashboard

More languages + voice styles

🏁 Conclusion

You now have a fully production-ready multilingual TTS system with:

✨ Natural speech quality

🔁 Automatic translation

🔒 Magic-link authentication

🧩 Scalable backend

🎨 Clean UI
