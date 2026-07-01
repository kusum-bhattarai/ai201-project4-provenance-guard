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
from detection import llm_signal

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

    # Signal 1 — LLM classifier.
    llm = llm_signal(text)

    # Placeholder confidence & label until Milestone 4/5. For now surface Signal 1's
    # score directly so the pipeline is inspectable end-to-end.
    confidence = llm["score"]
    attribution = "likely_ai" if confidence >= 0.7 else "likely_human" if confidence <= 0.35 else "uncertain"
    label = "PLACEHOLDER — real transparency label added in Milestone 5"

    audit.write_entry(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "event": "classified",
            "attribution": attribution,
            "confidence": round(confidence, 4),
            "llm_score": round(llm["score"], 4),
            "style_score": None,  # populated in Milestone 4
            "status": "classified",
            "label": label,
            "llm_rationale": llm["rationale"],
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": round(confidence, 4),
            "signals": {"llm_score": round(llm["score"], 4), "style_score": None},
            "label": label,
        }
    )


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": audit.get_log(limit=limit)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
