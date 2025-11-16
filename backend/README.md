# Linguavoice AI — Multilingual Text-to-Speech (International Edition)

## Overview
Linguavoice AI is a multilingual Text-to-Speech (TTS) platform enabling users to:

- Input text in **any language**
- Translate it automatically into the selected **target language**
- Generate high-quality spoken audio using **OpenAI TTS** with **HuggingFace fallback**
- Use a **magic-link login system** powered by **Supabase**
- Support freemium usage (anonymous 500-char limit)

The app includes a **Gradio frontend** deployed on HuggingFace and a **FastAPI backend** deployed on Render.

## Features
### Frontend (HuggingFace)
- Text input, language selection, voice selection
- Magic link login UI with auto token fill
- Audio preview & MP3 download
- Calls backend /generate endpoint

### Backend (Render)
- Translation (OpenAI GPT-3.5)
- Language detection & normalization
- TTS: OpenAI primary, HuggingFace fallback
- Magic link endpoints:
  - /auth/send_magic_link
  - /auth/create_magic_session
  - /auth/receive_token
  - /auth/poll_token
  - /auth_callback page

## Supabase Configuration
- Set **Site URL** = https://sumitg1979-international-multilingual-tts.hf.space
- Add Redirect URL:
  - https://linguavoice-backend.onrender.com/auth_callback
- Tables created:
  <img width="1919" height="421" alt="image" src="https://github.com/user-attachments/assets/deac692a-bb02-4654-804e-4c88dc6a71fa" />
- API Keys:
  <img width="1885" height="840" alt="image" src="https://github.com/user-attachments/assets/94a7cf14-4904-447e-8298-57c6657a7091" />

  
## Environment Variables
### Render (Backend)
<img width="1221" height="591" alt="image" src="https://github.com/user-attachments/assets/b1397ffa-b715-4e66-821d-21e609029640" />


### HuggingFace (Frontend)
BACKEND_URL=https://linguavoice-backend.onrender.com
<img width="1363" height="345" alt="image" src="https://github.com/user-attachments/assets/e2e9ecdb-d0e6-43c8-90ea-cf836ea4ef04" />

## Magic Link Authentication — Architecture Diagram
                ┌────────────────────────────────────────┐
                │          HuggingFace Frontend          │
                │        (Gradio – app.py UI)            │
                └────────────────────────────────────────┘
                               │
                               │ 1. User enters email
                               ▼
                ┌────────────────────────────────────────┐
                │   POST /auth/send_magic_link (backend) │
                │   Trigger email magic link in Supabase │
                └────────────────────────────────────────┘
                               │
                               │ 2. Backend calls
                               ▼
                 ┌─────────────────────────────────────┐
                 │             Supabase Auth            │
                 │ sends Magic Link email to the user  │
                 └─────────────────────────────────────┘
                               │
                               │ 3. User clicks magic link
                               ▼
      ┌────────────────────────────────────────────────────────────────┐
      │ Supabase redirects user to:                                     │
      │                                                                │
      │   https://linguavoice-backend.onrender.com/auth_callback       │
      └────────────────────────────────────────────────────────────────┘
                               │
                               │ 4. auth_callback extracts token
                               │    using Supabase JS in HTML
                               ▼
                 ┌────────────────────────────────────────┐
                 │  POST /auth/receive_token (backend)    │
                 │  Backend stores the token in memory    │
                 │  keyed by random session_key           │
                 └────────────────────────────────────────┘
                               │
                               │ 5. auth_callback redirects user
                               │    back to HuggingFace Space:
                               ▼
     ┌──────────────────────────────────────────────────────────┐
     │ https://sumitg1979-international-multilingual-tts.hf.space │
     │           ?session_key=<generated_key>                   │
     └──────────────────────────────────────────────────────────┘
                               │
                               │ 6. HF Space auto-polls backend:
                               ▼
                 ┌────────────────────────────────────────┐
                 │ GET /auth/poll_token?key=session_key   │
                 │ Returns JWT token if ready              │
                 └────────────────────────────────────────┘
                               │
                               │ 7. HF Space fills token box
                               │    and enables premium usage
                               ▼
           ┌──────────────────────────────────────────────┐
           │   User is authenticated inside the frontend   │
           │   JWT token now sent automatically on /generate│
           └──────────────────────────────────────────────┘
⭐ High-Level Summary
Frontend (HF Space)

User enters email

Calls backend /auth/send_magic_link

Polls /auth/poll_token

Auto-fills token field → logged in

Supabase

Sends Magic Link

Redirects user to backend callback

Provides JWT session to backend

Backend (Render FastAPI)

/auth/send_magic_link → triggers Supabase email

/auth_callback → receives browser → extracts token

/auth/receive_token → stores token

/auth/poll_token → front-end checks if token is ready

Adds token to authenticated calls (/generate)

## Usage
1. Enter text in any language  
2. Choose target language  
3. (Optional) Login using magic link  
4. Click **Generate Speech**  
5. Listen or download the MP3  

## Local Development
### Backend
```
pip install fastapi uvicorn requests python-multipart
uvicorn main:app --reload
```

### Frontend
```
pip install gradio requests
python app.py
```

## Future Enhancements
- Stripe subscriptions  
- Usage quotas with Supabase  
- More languages & voice styles  
