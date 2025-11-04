# 🧠 Linguavoice Backend (FastAPI)

This backend handles secure Text-to-Speech requests from the Hugging Face frontend and calls OpenAI’s TTS API.

---

## 🚀 Deploy on Render

### 1️⃣ Create a new Web Service
- Log in to [Render Dashboard](https://dashboard.render.com)
- Click **“+ New” → “Web Service”**
- Connect your GitHub repo (`linguavoice-backend`)
- Select **Environment: Python 3**

---

### 2️⃣ Configure Build & Start Commands
**Build Command**
```bash
pip install -r requirements.txt

