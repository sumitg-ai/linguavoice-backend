from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
import os
import tempfile

# Initialize app
app = FastAPI(title="Linguavoice Backend API")

# Allow requests from Hugging Face Spaces frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict later to HF Space domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Define request model
class TTSRequest(BaseModel):
    text: str
    language: str
    voice: str = "alloy"  # default voice

@app.get("/health")
def health_check():
    """Simple health endpoint for Render uptime checks."""
    return {"status": "ok", "service": "Linguavoice Backend"}

@app.post("/generate")
async def generate_tts(request: TTSRequest):
    """
    Generate Text-to-Speech using OpenAI API.
    Returns a URL to the MP3 file generated.
    """
    try:
        # Make OpenAI TTS request
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            response = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=request.voice,
                input=request.text
            )
            response.stream_to_file(tmp.name)

            # Return the audio file as a temporary URL
            return {"status": "success", "file_path": tmp.name}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")
