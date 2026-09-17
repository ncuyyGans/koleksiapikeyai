"""Encrypted SQLite store for AI custom-model credentials.

The whole database file is encrypted at rest with Fernet (AES-128-CBC + HMAC)
so it is safe to commit back to a public repository as a backup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DB_PATH = Path(os.environ.get("DB_PATH", "data.db.enc"))
KEY_ENV = "DB_ENCRYPTION_KEY"

_lock = threading.RLock()  # reentrant: public API locks, then _load() -> init_db() re-locks


def _load_key() -> bytes:
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"Environment variable {KEY_ENV} is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\""
        )
    return raw.encode()


def _read_plain() -> bytes:
    if not DB_PATH.exists():
        return b""
    return DB_PATH.read_bytes()


def _write_plain(data: bytes) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(data)


def _decrypt() -> bytes:
    blob = _read_plain()
    if not blob:
        return b""
    try:
        return Fernet(_load_key()).decrypt(blob)
    except InvalidToken as exc:  # pragma: no cover - fatal misconfiguration
        raise RuntimeError("DB_ENCRYPTION_KEY does not match the encrypted database file.") from exc


def _encrypt(data: bytes) -> bytes:
    return Fernet(_load_key()).encrypt(data)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    provider      TEXT NOT NULL DEFAULT '',
    base_url      TEXT NOT NULL DEFAULT '',
    api_key       TEXT NOT NULL DEFAULT '',
    model_id      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    pinned        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    user_id       INTEGER NOT NULL,
    action        TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT ''
);
"""


def init_db() -> None:
    """Ensure the store directory exists and the encryption key is readable.

    The schema itself is created lazily by :func:`_load` on the connection
    that is actually used (a brand-new store has no dump to restore).
    """
    if DB_PATH.parent and not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _load_key()  # fail fast on a missing/misconfigured key


def _load() -> sqlite3.Connection:
    init_db()
    conn = _connect()
    plain = _decrypt()
    if plain:
        # Restore the previously persisted database dump.
        conn.executescript(plain.decode("utf-8"))
    else:
        # Brand-new store: no dump yet, so create the schema in place.
        conn.executescript(_SCHEMA)
    return conn


def _save(conn: sqlite3.Connection) -> None:
    dump = "".join(conn.iterdump()).encode("utf-8")
    _write_plain(_encrypt(dump))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def add_model(
    name: str,
    provider: str = "",
    base_url: str = "",
    api_key: str = "",
    model_id: str = "",
    notes: str = "",
    tags: str = "",
) -> None:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    now = _now()
    with _lock, _load() as conn:
        conn.execute(
            """INSERT INTO models (name, provider, base_url, api_key, model_id, notes, tags, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, provider.strip(), base_url.strip(), api_key.strip(), model_id.strip(),
             notes.strip(), tags.strip(), now, now),
        )
        _save(conn)


def upsert_model(
    name: str,
    provider: str = "",
    base_url: str = "",
    api_key: str = "",
    model_id: str = "",
    notes: str = "",
    tags: str = "",
) -> bool:
    """Insert or update. Returns True if a new row was created."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    now = _now()
    with _lock, _load() as conn:
        row = conn.execute("SELECT 1 FROM models WHERE name = ?", (name,)).fetchone()
        if row:
            conn.execute(
                """UPDATE models
                      SET provider = COALESCE(NULLIF(?, ''), provider),
                          base_url = COALESCE(NULLIF(?, ''), base_url),
                          api_key  = COALESCE(NULLIF(?, ''), api_key),
                          model_id = COALESCE(NULLIF(?, ''), model_id),
                          notes    = COALESCE(NULLIF(?, ''), notes),
                          tags     = COALESCE(NULLIF(?, ''), tags),
                          updated_at = ?
                    WHERE name = ?""",
                (provider.strip(), base_url.strip(), api_key.strip(), model_id.strip(),
                 notes.strip(), tags.strip(), now, name),
            )
            created = False
        else:
            conn.execute(
                """INSERT INTO models (name, provider, base_url, api_key, model_id, notes, tags, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, provider.strip(), base_url.strip(), api_key.strip(), model_id.strip(),
                 notes.strip(), tags.strip(), now, now),
            )
            created = True
        _save(conn)
        return created


def list_models() -> list[dict]:
    with _lock, _load() as conn:
        rows = conn.execute(
            "SELECT * FROM models ORDER BY pinned DESC, name COLLATE NOCASE ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_model(name: str) -> dict | None:
    with _lock, _load() as conn:
        row = conn.execute("SELECT * FROM models WHERE name = ?", (name.strip(),)).fetchone()
        return dict(row) if row else None


def update_field(name: str, field: str, value: str) -> bool:
    allowed = {"provider", "base_url", "api_key", "model_id", "notes", "tags", "name"}
    if field not in allowed:
        raise ValueError(f"field must be one of: {', '.join(sorted(allowed))}")
    with _lock, _load() as conn:
        row = conn.execute("SELECT 1 FROM models WHERE name = ?", (name.strip(),)).fetchone()
        if not row:
            return False
        conn.execute(
            f"UPDATE models SET {field} = ?, updated_at = ? WHERE name = ?",
            (value.strip(), _now(), name.strip()),
        )
        _save(conn)
        return True


def delete_model(name: str) -> bool:
    with _lock, _load() as conn:
        cur = conn.execute("DELETE FROM models WHERE name = ?", (name.strip(),))
        _save(conn)
        return cur.rowcount > 0


def toggle_pin(name: str) -> bool | None:
    """Toggle pinned flag. Returns the new state, or None if not found."""
    with _lock, _load() as conn:
        row = conn.execute("SELECT pinned FROM models WHERE name = ?", (name.strip(),)).fetchone()
        if not row:
            return None
        new = 0 if row["pinned"] else 1
        conn.execute("UPDATE models SET pinned = ? WHERE name = ?", (new, name.strip()))
        _save(conn)
        return bool(new)


def search_models(query: str) -> list[dict]:
    q = f"%{query.strip().lower()}%"
    with _lock, _load() as conn:
        rows = conn.execute(
            """SELECT * FROM models
                WHERE LOWER(name) LIKE ? OR LOWER(provider) LIKE ? OR LOWER(tags) LIKE ?
                   OR LOWER(model_id) LIKE ? OR LOWER(notes) LIKE ?
             ORDER BY pinned DESC, name COLLATE NOCASE ASC""",
            (q, q, q, q, q),
        ).fetchall()
        return [dict(r) for r in rows]


def add_audit(user_id: int, action: str, detail: str = "") -> None:
    with _lock, _load() as conn:
        conn.execute(
            "INSERT INTO audit (ts, user_id, action, detail) VALUES (?,?,?,?)",
            (_now(), user_id, action, detail[:500]),
        )
        _save(conn)


def list_audit(limit: int = 20) -> list[dict]:
    with _lock, _load() as conn:
        rows = conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def export_all() -> list[dict]:
    return list_models()


def import_payload(payload: list[dict]) -> tuple[int, int]:
    """Import a list of model dicts. Returns (added, updated)."""
    added = updated = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("Nama") or "").strip()
        if not name:
            continue
        created = upsert_model(
            name,
            provider=str(item.get("provider") or item.get("API Provider") or ""),
            base_url=str(item.get("base_url") or item.get("Base URL") or ""),
            api_key=str(item.get("api_key") or item.get("API Key") or ""),
            model_id=str(item.get("model_id") or item.get("Model ID") or ""),
            notes=str(item.get("notes") or item.get("Catatan") or ""),
            tags=str(item.get("tags") or item.get("Tags") or ""),
        )
        if created:
            added += 1
        else:
            updated += 1
    return added, updated


def db_exists() -> bool:
    return DB_PATH.exists() and _read_plain() != b""