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
- Set **Site URL** to HF Space URL
- Add Redirect URL:
  - https://<backend>/auth_callback

## Environment Variables
### Render (Backend)
<img width="1221" height="591" alt="image" src="https://github.com/user-attachments/assets/b1397ffa-b715-4e66-821d-21e609029640" />


### HuggingFace (Frontend)
BACKEND_URL=https://linguavoice-backend.onrender.com
<img width="1363" height="345" alt="image" src="https://github.com/user-attachments/assets/e2e9ecdb-d0e6-43c8-90ea-cf836ea4ef04" />

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
