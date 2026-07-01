"""Provenance Guard — Flask API.

Milestone 3: POST /submit wired to Signal 1 (Groq LLM) with placeholder confidence and
label, structured audit logging, GET /log, GET /health.
Milestones 4 & 5 add the second signal, real confidence scoring, transparency labels,
the appeal endpoint, and rate limiting.
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import audit
from detection import llm_signal, score_confidence, stylometry_signal

load_dotenv()

app = Flask(__name__)
audit.init_db()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
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
    label = "PLACEHOLDER — real transparency label added in Milestone 5"

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
            "label": label,
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


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": audit.get_log(limit=limit)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
