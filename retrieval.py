import re
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent / "data" / "student_handbook.md"

def _load_chunks():
    if not POLICY_PATH.exists():
        return []
    text = POLICY_PATH.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.splitlines()
        heading = lines[0].strip().lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        if body:
            # Keep each policy section as a compact retrievable unit.
            chunks.append({"source": POLICY_PATH.name, "heading": heading, "text": body})
    return chunks

def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))

def retrieve_policy_chunks(query, top_k=3):
    query_tokens = _tokens(query)
    chunks = _load_chunks()
    scored = []
    for chunk in chunks:
        body_tokens = _tokens(chunk["heading"] + " " + chunk["text"])
        overlap = len(query_tokens & body_tokens)
        # Slight boost for exact topic phrase matches.
        combined = (chunk["heading"] + " " + chunk["text"]).lower()
        phrase_boost = 2 if any(phrase in combined for phrase in [
            "payment portal", "late fee", "course change", "scholarship", "refund", "support request"
        ] if phrase in query.lower()) else 0
        score = overlap + phrase_boost
        if score > 0:
            scored.append({**chunk, "score": score})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]
