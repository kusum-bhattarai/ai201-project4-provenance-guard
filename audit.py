"""Structured audit log backed by SQLite.

Every attribution decision and every appeal is written here as one row. The schema
carries both individual signal scores and the combined confidence so the log is a full,
replayable record of each decision (required feature: structured audit log).
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = "audit.db"


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT NOT NULL,
                creator_id        TEXT,
                timestamp         TEXT NOT NULL,
                event             TEXT NOT NULL,          -- 'classified' | 'appeal'
                attribution       TEXT,                   -- likely_ai | uncertain | likely_human
                confidence        REAL,                   -- combined P(AI) in [0,1]
                llm_score         REAL,
                style_score       REAL,
                phrase_score      REAL,
                status            TEXT,                   -- classified | under_review
                label             TEXT,
                appeal_reasoning  TEXT,
                extra             TEXT                    -- JSON blob for anything else
            )
            """
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_entry(entry: dict, db_path: str = DB_PATH) -> None:
    """Insert one structured audit entry. Unknown keys are folded into `extra` JSON."""
    columns = {
        "content_id", "creator_id", "timestamp", "event", "attribution", "confidence",
        "llm_score", "style_score", "phrase_score", "status", "label", "appeal_reasoning",
    }
    row = {k: entry.get(k) for k in columns}
    row.setdefault("timestamp", None)
    if not row["timestamp"]:
        row["timestamp"] = _now_iso()
    extras = {k: v for k, v in entry.items() if k not in columns}
    row["extra"] = json.dumps(extras) if extras else None

    with closing(_connect(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, event, attribution, confidence,
                 llm_score, style_score, phrase_score, status, label, appeal_reasoning, extra)
            VALUES
                (:content_id, :creator_id, :timestamp, :event, :attribution, :confidence,
                 :llm_score, :style_score, :phrase_score, :status, :label, :appeal_reasoning, :extra)
            """,
            row,
        )


def get_log(limit: int = 50, db_path: str = DB_PATH) -> list[dict]:
    """Return the most recent audit entries (newest first) as plain dicts."""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    entries = []
    for r in rows:
        d = dict(r)
        if d.get("extra"):
            d["extra"] = json.loads(d["extra"])
        entries.append(d)
    return entries


def get_latest_for_content(content_id: str, db_path: str = DB_PATH) -> dict | None:
    """Most recent audit entry for a content_id (used to look up the original decision)."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM audit_log WHERE content_id = ? ORDER BY id DESC LIMIT 1",
            (content_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("extra"):
        d["extra"] = json.loads(d["extra"])
    return d
