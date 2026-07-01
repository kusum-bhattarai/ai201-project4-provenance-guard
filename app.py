"""Provenance Guard — Flask API.

Milestone 3: POST /submit wired to Signal 1 (Groq LLM) with placeholder confidence and
label, structured audit logging, GET /log, GET /health.
Milestones 4 & 5 add the second signal, real confidence scoring, transparency labels,
the appeal endpoint, and rate limiting.
"""

import os
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import audit
from detection import llm_signal, score_confidence, stylometry_signal
from labels import generate_label

load_dotenv()

app = Flask(__name__)
audit.init_db()

# Rate limiting. Limits are per client IP. Chosen values (see README for reasoning):
#   10/minute  — generous for a real creator submitting their own work, but throttles
#                scripted bursts.
#   100/hour   — a sane daily-ish ceiling for one creator; blocks sustained flooding.
# In-memory storage is fine for local/dev; use Redis in production.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify({"error": "Rate limit exceeded. Please slow down and try again shortly.",
                 "detail": str(e.description)}),
        429,
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per hour")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be a non-empty string."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "Field 'creator_id' is required and must be a non-empty string."}), 400

    content_id = str(uuid.uuid4())

    # Multi-signal detection pipeline.
    llm = llm_signal(text)                     # Signal 1 — semantic (Groq)
    style = stylometry_signal(text)            # Signal 2 — structural (pure Python)
    scored = score_confidence(llm["score"], style["score"])  # combine -> P(AI)

    confidence = scored["confidence"]
    attribution = scored["attribution"]
    label = generate_label(confidence)

    audit.write_entry(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "event": "classified",
            "attribution": attribution,
            "confidence": confidence,
            "llm_score": llm["score"],
            "style_score": style["score"],
            "status": "classified",
            "label": label["display_text"],
            "llm_rationale": llm["rationale"],
            "disagreement": scored["disagreement"],
            "style_metrics": style["metrics"],
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": confidence,
            "signals": {"llm_score": llm["score"], "style_score": style["score"]},
            "label": label,
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required."}), 400

    original = audit.get_latest_for_content(content_id)
    if original is None:
        return jsonify({"error": f"No content found with id '{content_id}'."}), 404

    # Log the appeal alongside the original decision (original scores preserved) and
    # move the content's status to under_review. No automated re-classification.
    audit.write_entry(
        {
            "content_id": content_id,
            "creator_id": original.get("creator_id"),
            "event": "appeal",
            "attribution": original.get("attribution"),
            "confidence": original.get("confidence"),
            "llm_score": original.get("llm_score"),
            "style_score": original.get("style_score"),
            "status": "under_review",
            "label": original.get("label"),
            "appeal_reasoning": creator_reasoning,
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": "Your appeal has been received and this content is now under review.",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": audit.get_log(limit=limit)})


if __name__ == "__main__":
    # Default 5000 to match the project spec. On macOS, AirPlay Receiver also uses 5000;
    # set PORT=5001 (or disable AirPlay Receiver) if you hit 403s.
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
