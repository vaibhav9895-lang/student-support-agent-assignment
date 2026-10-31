"""Checks your Groq setup WITHOUT printing your key.  Run:  python check_setup.py"""
import os
from pathlib import Path
from dotenv import load_dotenv

env_file = Path(__file__).resolve().parent / ".env"
print(".env file found:", env_file.exists(), "->", env_file)
shell_key = os.getenv("GROQ_API_KEY")
load_dotenv(env_file, override=True)          # same rule the app uses: .env wins
key = os.getenv("GROQ_API_KEY") or ""
print("Key loaded:", bool(key), "| length:", len(key), "| starts with 'gsk_':", key.startswith("gsk_"))
if shell_key and shell_key != key:
    print("NOTE: an OLD key was set in this Terminal session; the .env key is used instead.")
print("Placeholder still in .env:", "paste_your" in key or key == "your_new_groq_api_key")
model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
print("Model:", model, "| Endpoint: https://api.groq.com/openai/v1")
try:
    from openai import OpenAI
    r = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1").chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Say OK"}], max_tokens=20)
    print("Groq call: SUCCESS")
except Exception as e:
    print("Groq call FAILED:", type(e).__name__, str(e)[:160].replace(key, "***") if key else str(e)[:160])
