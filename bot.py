"""Telegram bot: personal vault for AI custom-model credentials (Cline format).

Runs as a long-polling loop. Designed to be supervised by the GitHub Actions
workflow (auto-restart on crash, auto-exit before the 6h job limit).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").replace(";", ",").split(",") if x.strip()
}
REVEAL_PIN = os.environ.get("REVEAL_PIN", "").strip()
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_ID = next(iter(ALLOWED_USERS), None)

# Exit codes used by the supervisor in the workflow.
EXIT_RESTART = 10
EXIT_ROTATE = 20

MAX_MSG = 4000  # Telegram hard limit is 4096; keep a safety margin.

# --------------------------------------------------------------------------- #
# Telegram raw API (stdlib only, no external deps)
# --------------------------------------------------------------------------- #


def _multipart(payload: dict, files: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data body. files: {field: (filename, bytes, mime)}."""
    boundary = "----koleksiapikeyai" + os.urandom(16).hex()
    parts: list[bytes] = []
    for key, value in payload.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode(
                "utf-8"
            )
        )
    for key, (filename, content, mime) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
            f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8")
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _tg(
    method: str,
    payload: dict | None = None,
    files: dict | None = None,
    timeout: int = 60,
) -> dict:
    url = f"{API_BASE}/{method}"
    if files:
        body, content_type = _multipart(payload or {}, files)
        req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    else:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        log.warning("HTTP %s from %s: %s", exc.code, method, body[:300])
        return {"ok": False, "error_code": exc.code, "description": body[:300]}
    except Exception as exc:  # noqa: BLE001 - network hiccups must not kill the bot
        log.warning("request to %s failed: %s", method, exc)
        return {"ok": False, "description": str(exc)}


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    text = text or " "
    if len(text) > MAX_MSG:
        text = text[: MAX_MSG - 20] + "\n…(dipotong)"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _tg("sendMessage", payload)


def answer_callback(callback_id: str, text: str = "") -> None:
    _tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text or " ",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _tg("editMessageText", payload)


def delete_message(chat_id: int, message_id: int) -> None:
    _tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

FIELDS = [
    ("provider", "API Provider"),
    ("base_url", "Base URL"),
    ("api_key", "API Key"),
    ("model_id", "Model ID"),
    ("notes", "Catatan"),
    ("tags", "Tags"),
]


def esc(text: str) -> str:
    """Escape HTML special characters for Telegram's HTML parse mode."""
    amp = chr(38) + "amp;"
    lt = chr(38) + "lt;"
    gt = chr(38) + "gt;"
    return str(text).replace(chr(38), amp).replace("<", lt).replace(">", gt)


def mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 8:
        return "••••" if key else "<i>kosong</i>"
    return f"{key[:4]}••••{key[-4:]}"


def authorized(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # not configured -> open (not recommended)
    return user_id in ALLOWED_USERS


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def model_line(m: dict, index: int) -> str:
    pin = "📌 " if m.get("pinned") else ""
    key = mask_key(m.get("api_key") or "")
    return (
        f"{pin}<b>{index}. {esc(m['name'])}</b>\n"
        f"   • Provider: <code>{esc(m.get('provider') or '-')}</code>\n"
        f"   • Base URL: <code>{esc(m.get('base_url') or '-')}</code>\n"
        f"   • API Key: <code>{esc(key)}</code>\n"
        f"   • Model ID: <code>{esc(m.get('model_id') or '-')}</code>"
    )


def view_keyboard(name: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔑 Salin API Key", "callback_data": f"copy:api_key:{name}"},
                {"text": "🔗 Salin Base URL", "callback_data": f"copy:base_url:{name}"},
            ],
            [
                {"text": "🆔 Salin Model ID", "callback_data": f"copy:model_id:{name}"},
                {"text": "📋 Salin Provider", "callback_data": f"copy:provider:{name}"},
            ],
            [
                {"text": "📌 Pin/Unpin", "callback_data": f"pin:{name}"},
                {"text": "🗑 Hapus", "callback_data": f"del:{name}"},
            ],
        ]
    }


def list_keyboard(names: list[str], prefix: str = "view") -> dict:
    rows = [[{"text": n, "callback_data": f"{prefix}:{n}"} for n in names[i : i + 2]]
            for i in range(0, len(names), 2)]
    return {"inline_keyboard": rows}


# --------------------------------------------------------------------------- #
# Conversational /add flow
# --------------------------------------------------------------------------- #

# chat_id -> {"step": ..., "data": {...}}
flows: dict[int, dict] = {}
flows_lock = threading.Lock()

ADD_STEPS = [
    ("provider", "API Provider (mis. OpenRouter / DeepInfra)? Balas atau /skip."),
    ("base_url", "Base URL (mis. https://openrouter.ai/api/v1)? Balas atau /skip."),
    ("api_key", "API Key? Balas dengan key-nya (bisa /skip dulu, isi nanti via /edit)."),
    ("model_id", "Model ID (mis. anthropic/claude-3.5-sonnet)? Balas atau /skip."),
    ("notes", "Catatan tambahan? Balas atau /skip."),
    ("tags", "Tags dipisah koma (mis. murah,cepat)? Balas atau /skip."),
]


def start_add(chat_id: int, name: str) -> None:
    with flows_lock:
        flows[chat_id] = {"step": "provider", "data": {"name": name}}
    label, question = ADD_STEPS[0]
    send_message(
        chat_id,
        f"➕ <b>Tambah model: {esc(name)}</b>\n\n"
        f"Kita isi field satu per satu. Ketik nilainya, atau <code>/skip</code> untuk lewati, "
        f"<code>/cancel</code> untuk batal.\n\n<b>{label}</b> — {question}",
    )


def continue_add(chat_id: int, text: str) -> bool:
    """Advance the /add flow. Returns True if the message was consumed."""
    with flows_lock:
        flow = flows.get(chat_id)
    if not flow:
        return False

    step = flow["step"]
    value = "" if text.strip() == "/skip" else text.strip()

    if value:
        flow["data"][step] = value

    order = [s for s, _ in ADD_STEPS]
    try:
        idx = order.index(step)
    except ValueError:
        idx = -1

    if idx + 1 >= len(order):
        data = flow["data"]
        with flows_lock:
            flows.pop(chat_id, None)
        created = db.upsert_model(
            data["name"],
            provider=data.get("provider", ""),
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            model_id=data.get("model_id", ""),
            notes=data.get("notes", ""),
            tags=data.get("tags", ""),
        )
        db.add_audit(chat_id, "add", data["name"])
        send_message(
            chat_id,
            f"{'✅ Ditambahkan' if created else '♻️ Diperbarui'}: <b>{esc(data['name'])}</b>\n"
            "Ketik <code>/list</code> untuk melihat semua model.",
        )
        return True

    nxt = order[idx + 1]
    with flows_lock:
        if chat_id in flows:
            flows[chat_id]["step"] = nxt
    label, question = ADD_STEPS[idx + 1]
    send_message(chat_id, f"<b>{esc(label)}</b> — {question}")
    return True


def cancel_flow(chat_id: int) -> bool:
    with flows_lock:
        return flows.pop(chat_id, None) is not None


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


def cmd_start(chat_id: int) -> None:
    send_message(
        chat_id,
        "🔐 <b>Koleksi API Key AI</b>\n\n"
        "Brankas pribadi untuk custom model AI (format Cline):\n"
        "API Provider • Base URL • API Key • Model ID\n\n"
        "<b>Perintah:</b>\n"
        "<code>/add <nama></code> — tambah model (isi interaktif)\n"
        "<code>/list</code> — daftar model (key disembunyikan)\n"
        "<code>/view <nama></code> — detail + tombol salin\n"
        "<code>/search <kata></code> — cari model\n"
        "<code>/edit <nama> <field> <nilai></code>\n"
        "<code>/delete <nama></code> — hapus\n"
        "<code>/pin <nama></code> — sematkan favorit\n"
        "<code>/export</code> — export JSON\n"
        "<code>/import</code> — import JSON (balas file JSON)\n"
        "<code>/audit</code> — log aktivitas\n"
        "<code>/help</code> — panduan lengkap\n\n"
        "💡 <i>API Key hanya tampil penuh setelah PIN benar.</i>",
    )


def cmd_help(chat_id: int) -> None:
    send_message(
        chat_id,
        "📖 <b>Panduan</b>\n\n"
        "<b>1. Tambah model</b>\n"
        "<code>/add deepseek-v3</code> lalu ikuti pertanyaan (provider, base url, key, model id, catatan, tags). "
        "Gunakan <code>/skip</code> untuk lewati, <code>/cancel</code> untuk batal.\n\n"
        "<b>2. Lihat & salin</b>\n"
        "<code>/list</code> → ketuk nama model → tombol <b>Salin API Key</b> (butuh PIN).\n"
        "Sama cepatnya: <code>/view deepseek-v3</code>.\n\n"
        "<b>3. Edit satu field</b>\n"
        "<code>/edit deepseek-v3 api_key sk-xxxx</code>\n"
        "Field: <code>provider base_url api_key model_id notes tags name</code>\n\n"
        "<b>4. Import/export</b>\n"
        "<code>/export</code> → file JSON. Balas file itu dengan <code>/import</code> untuk memulihkan.\n\n"
        "<b>5. Keamanan</b>\n"
        "• Hanya user ID terdaftar yang bisa akses.\n"
        "• DB terenkripsi (Fernet/AES) sebelum dicommit ke repo.\n"
        "• PIN wajib untuk melihat API Key utuh.\n"
        "• <b>Ganti token di @BotFather</b> kalau bot ini pernah terbuka orang lain.",
    )


def cmd_add(chat_id: int, args: str) -> None:
    name = args.strip()
    if not name:
        send_message(
            chat_id,
            "Format: <code>/add <nama-model></code>\nContoh: <code>/add deepseek-v3</code>",
        )
        return
    if len(name) > 60:
        send_message(chat_id, "Nama terlalu panjang (maks 60 karakter).")
        return
    start_add(chat_id, name)


def cmd_list(chat_id: int) -> None:
    models = db.list_models()
    if not models:
        send_message(chat_id, "📭 Belum ada model. Ketik <code>/add <nama></code> untuk menambah.")
        return
    lines = [f"🗂 <b>{len(models)} model tersimpan</b> (klik nama untuk detail)\n"]
    for i, m in enumerate(models, 1):
        lines.append(model_line(m, i))
    text = "\n\n".join(lines)
    names = [m["name"] for m in models]
    send_message(chat_id, text, reply_markup=list_keyboard(names))


def cmd_view(chat_id: int, args: str) -> None:
    name = args.strip()
    if not name:
        send_message(chat_id, "Format: <code>/view <nama-model></code>")
        return
    m = db.get_model(name)
    if not m:
        send_message(chat_id, f"❌ Model <b>{esc(name)}</b> tidak ditemukan.")
        return
    send_view(chat_id, m)


def send_view(chat_id: int, m: dict) -> None:
    text = (
        f"🔐 <b>{esc(m['name'])}</b>"
        + (" 📌" if m.get("pinned") else "")
        + "\n\n"
        f"<b>API Provider</b>\n<code>{esc(m.get('provider') or '-')}</code>\n\n"
        f"<b>Base URL</b>\n<code>{esc(m.get('base_url') or '-')}</code>\n\n"
        f"<b>API Key</b>\n<code>{esc(mask_key(m.get('api_key') or ''))}</code>\n\n"
        f"<b>Model ID</b>\n<code>{esc(m.get('model_id') or '-')}</code>\n\n"
        f"<b>Catatan</b>\n{esc(m.get('notes') or '-')}\n\n"
        f"<b>Tags</b>\n<code>{esc(m.get('tags') or '-')}</code>\n\n"
        f"<i>Diperbarui: {esc(m.get('updated_at') or '?')}</i>\n"
        "<i>Ketuk tombol di bawah untuk menyalin. API Key butuh PIN.</i>"
    )
    send_message(chat_id, text, reply_markup=view_keyboard(m["name"]))


def cmd_search(chat_id: int, args: str) -> None:
    q = args.strip()
    if not q:
        send_message(chat_id, "Format: <code>/search <kata-kunci></code>")
        return
    models = db.search_models(q)
    if not models:
        send_message(chat_id, f"🔍 Tidak ada hasil untuk <b>{esc(q)}</b>.")
        return
    lines = [f"🔍 Hasil untuk <b>{esc(q)}</b> — {len(models)} model\n"]
    for i, m in enumerate(models, 1):
        lines.append(model_line(m, i))
    send_message(
        chat_id,
        "\n\n".join(lines),
        reply_markup=list_keyboard([m["name"] for m in models]),
    )


def cmd_edit(chat_id: int, args: str) -> None:
    parts = args.split(None, 2)
    if len(parts) < 3:
        send_message(
            chat_id,
            "Format: <code>/edit <nama> <field> <nilai></code>\n"
            "Field: provider, base_url, api_key, model_id, notes, tags, name\n"
            "Contoh: <code>/edit deepseek api_key sk-baru</code>",
        )
        return
    name, field, value = parts[0], parts[1].lower(), parts[2]
    try:
        ok = db.update_field(name, field, value)
    except ValueError as exc:
        send_message(chat_id, f"❌ {esc(str(exc))}")
        return
    if not ok:
        send_message(chat_id, f"❌ Model <b>{esc(name)}</b> tidak ditemukan.")
        return
    db.add_audit(chat_id, "edit", f"{name}.{field}")
    send_message(chat_id, f"✅ <b>{esc(field)}</b> dari <b>{esc(name)}</b> diperbarui.")


def cmd_delete(chat_id: int, args: str) -> None:
    name = args.strip()
    if not name:
        send_message(chat_id, "Format: <code>/delete <nama-model></code>")
        return
    m = db.get_model(name)
    if not m:
        send_message(chat_id, f"❌ Model <b>{esc(name)}</b> tidak ditemukan.")
        return
    send_message(
        chat_id,
        f"⚠️ Hapus <b>{esc(name)}</b>?\nTindakan ini tidak bisa dibatalkan.",
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "🗑 Ya, hapus", "callback_data": f"confirm_del:{name}"},
                    {"text": "Batal", "callback_data": "noop"},
                ]
            ]
        },
    )


def cmd_pin(chat_id: int, args: str) -> None:
    name = args.strip()
    if not name:
        send_message(chat_id, "Format: <code>/pin <nama-model></code>")
        return
    result = db.toggle_pin(name)
    if result is None:
        send_message(chat_id, f"❌ Model <b>{esc(name)}</b> tidak ditemukan.")
        return
    db.add_audit(chat_id, "pin", name)
    send_message(
        chat_id,
        f"{'📌 Disematkan' if result else '📍 Sematan dilepas'}: <b>{esc(name)}</b>",
    )


def cmd_export(chat_id: int) -> None:
    models = db.export_all()
    payload = [
        {
            "name": m["name"],
            "provider": m.get("provider") or "",
            "base_url": m.get("base_url") or "",
            "api_key": m.get("api_key") or "",
            "model_id": m.get("model_id") or "",
            "notes": m.get("notes") or "",
            "tags": m.get("tags") or "",
            "pinned": bool(m.get("pinned")),
        }
        for m in models
    ]
    content = json.dumps(
        {
            "app": "koleksi-apikey-ai",
            "exported_at": now_str(),
            "models": payload,
        },
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    files = {
        "document": (
            f"models-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json",
            content,
            "application/json",
        )
    }
    res = _tg("sendDocument", {"chat_id": chat_id}, files=files)
    if not res.get("ok"):
        send_message(chat_id, "⚠️ Export gagal. Coba lagi nanti.")


def cmd_audit(chat_id: int) -> None:
    rows = db.list_audit(15)
    if not rows:
        send_message(chat_id, "📭 Belum ada aktivitas tercatat.")
        return
    lines = ["🕘 <b>Log aktivitas terakhir</b>\n"]
    for r in rows:
        lines.append(f"<code>{esc(r['ts'])}</code> • {esc(r['action'])} • {esc(r['detail'])}")
    send_message(chat_id, "\n".join(lines))


def cmd_stats(chat_id: int) -> None:
    models = db.list_models()
    with_keys = sum(1 for m in models if m.get("api_key"))
    send_message(
        chat_id,
        f"📊 <b>Statistik</b>\n"
        f"• Total model: <b>{len(models)}</b>\n"
        f"• Memiliki API Key: <b>{with_keys}</b>\n"
        f"• Disematkan: <b>{sum(1 for m in models if m.get('pinned'))}</b>\n"
        f"• Waktu server: <code>{now_str()}</code>",
    )


# --------------------------------------------------------------------------- #
# Callback (inline button) handler
# --------------------------------------------------------------------------- #


def handle_callback(cb: dict) -> None:
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    user_id = cb.get("from", {}).get("id")
    cb_id = cb.get("id") or ""
    if chat_id is None:
        return

    if not authorized(user_id):
        answer_callback(cb_id, "⛔ Tidak diizinkan")
        return

    if data == "noop":
        answer_callback(cb_id, "Dibatalkan")
        return

    if data.startswith("view:"):
        name = data.split(":", 1)[1]
        m = db.get_model(name)
        if not m:
            answer_callback(cb_id, "Model tidak ditemukan")
            return
        send_view(chat_id, m)
        answer_callback(cb_id)
        return

    if data.startswith("copy:"):
        _, field, name = data.split(":", 2)
        m = db.get_model(name)
        if not m:
            answer_callback(cb_id, "Model tidak ditemukan")
            return
        value = (m.get(field) or "").strip()
        if not value:
            answer_callback(cb_id, f"{field} kosong")
            return
        if field == "api_key" and REVEAL_PIN:
            with flows_lock:
                flows[chat_id] = {
                    "step": "await_pin",
                    "data": {"field": "api_key", "name": name, "value": value},
                }
            answer_callback(cb_id, "PIN diperlukan")
            send_message(chat_id, "🔑 Ketik PIN untuk membuka API Key:")
            return
        send_value(chat_id, field, value, name)
        answer_callback(cb_id, "Disalin ke chat")
        return

    if data.startswith("pin:"):
        name = data.split(":", 1)[1]
        result = db.toggle_pin(name)
        if result is None:
            answer_callback(cb_id, "Model tidak ditemukan")
            return
        db.add_audit(user_id, "pin", name)
        answer_callback(cb_id, "📌 Dipasang" if result else "📍 Dilepas")
        m = db.get_model(name)
        if m:
            send_view(chat_id, m)
        return

    if data.startswith("confirm_del:"):
        name = data.split(":", 1)[1]
        if db.delete_model(name):
            db.add_audit(user_id, "delete", name)
            answer_callback(cb_id, "Dihapus")
            try:
                delete_message(chat_id, msg.get("message_id"))
            except Exception:  # noqa: BLE001
                pass
            send_message(chat_id, f"🗑 <b>{esc(name)}</b> dihapus.")
        else:
            answer_callback(cb_id, "Sudah tidak ada")
        return

    if data.startswith("del:"):
        name = data.split(":", 1)[1]
        edit_message(
            chat_id,
            msg.get("message_id"),
            f"⚠️ Hapus <b>{esc(name)}</b>?\nTindakan ini tidak bisa dibatalkan.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "🗑 Ya, hapus", "callback_data": f"confirm_del:{name}"},
                        {"text": "Batal", "callback_data": "noop"},
                    ]
                ]
            },
        )
        answer_callback(cb_id)
        return

    answer_callback(cb_id, "?")


def send_value(chat_id: int, field: str, value: str, name: str) -> None:
    label = dict(FIELDS).get(field, field)
    send_message(
        chat_id,
        f"📋 <b>{esc(label)}</b> — {esc(name)}\n<code>{esc(value)}</code>\n\n"
        f"<i>Ketuk teks di atas untuk menyalin.</i>",
    )


# --------------------------------------------------------------------------- #
# Message dispatch
# --------------------------------------------------------------------------- #


def handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (message.get("from") or {}).get("id")
    text = (message.get("text") or "").strip()

    if chat_id is None:
        return

    # Document upload (for /import)
    doc = message.get("document")
    if doc and text.lower().startswith("/import"):
        handle_import(chat_id, user_id, doc)
        return

    if not text:
        return

    if not authorized(user_id):
        send_message(chat_id, "⛔ Bot ini bersifat pribadi. Anda tidak diizinkan.")
        return

    # Mid-flow answers take priority over commands (except /cancel & /skip handled below).
    with flows_lock:
        flow = flows.get(chat_id)
    if flow and flow.get("step") == "await_pin" and not text.startswith("/"):
        flows.pop(chat_id, None)
        if text == REVEAL_PIN:
            d = flow["data"]
            db.add_audit(user_id, "reveal", d["name"])
            send_value(chat_id, "api_key", d["value"], d["name"])
        else:
            send_message(chat_id, "❌ PIN salah. Akses ditolak.")
        return

    if flow and not text.startswith("/"):
        if text.lower() == "/cancel":
            cancel_flow(chat_id)
            send_message(chat_id, "↩️ Dibatalkan.")
            return
        if continue_add(chat_id, text):
            return

    if not text.startswith("/"):
        # Plain message outside a flow -> hint.
        send_message(
            chat_id,
            "Ketik <code>/list</code> untuk melihat model, atau <code>/help</code>.",
        )
        return

    parts = text.split(None, 1)
    cmd = parts[0].lower().lstrip("/")
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "cancel":
        cancel_flow(chat_id)
        send_message(chat_id, "↩️ Dibatalkan.")
        return
    if cmd == "skip":
        if flow:
            continue_add(chat_id, "/skip")
        else:
            send_message(chat_id, "Tidak ada proses yang berjalan.")
        return

    handlers = {
        "start": lambda: cmd_start(chat_id),
        "help": lambda: cmd_help(chat_id),
        "add": lambda: cmd_add(chat_id, args),
        "list": lambda: cmd_list(chat_id),
        "ls": lambda: cmd_list(chat_id),
        "view": lambda: cmd_view(chat_id, args),
        "show": lambda: cmd_view(chat_id, args),
        "search": lambda: cmd_search(chat_id, args),
        "find": lambda: cmd_search(chat_id, args),
        "edit": lambda: cmd_edit(chat_id, args),
        "delete": lambda: cmd_delete(chat_id, args),
        "del": lambda: cmd_delete(chat_id, args),
        "rm": lambda: cmd_delete(chat_id, args),
        "pin": lambda: cmd_pin(chat_id, args),
        "export": lambda: cmd_export(chat_id),
        "audit": lambda: cmd_audit(chat_id),
        "stats": lambda: cmd_stats(chat_id),
        "ping": lambda: send_message(chat_id, f"🏓 Pong! {now_str()}"),
    }
    handler = handlers.get(cmd)
    if handler:
        try:
            handler()
        except Exception as exc:  # noqa: BLE001
            log.exception("command /%s failed", cmd)
            send_message(chat_id, f"⚠️ Terjadi kesalahan: <code>{esc(str(exc))}</code>")
    else:
        send_message(
            chat_id,
            f"Perintah tidak dikenal: <code>/{esc(cmd)}</code>. Ketik <code>/help</code>.",
        )


def handle_import(chat_id: int, user_id: int, doc: dict) -> None:
    file_id = doc.get("file_id")
    if not file_id:
        send_message(chat_id, "⚠️ File tidak terbaca.")
        return
    res = _tg("getFile", {"file_id": file_id})
    if not res.get("ok"):
        send_message(chat_id, "⚠️ Gagal mengambil file.")
        return
    path = res["result"].get("file_path")
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{urllib.parse.quote(path)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read()
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and "models" in payload:
            payload = payload["models"]
        if not isinstance(payload, list):
            raise ValueError("format JSON harus berupa list atau {\"models\": [...]}")
    except Exception as exc:  # noqa: BLE001
        send_message(chat_id, f"❌ Gagal import: <code>{esc(str(exc))}</code>")
        return
    added, updated = db.import_payload(payload)
    db.add_audit(user_id, "import", f"+{added} ~{updated}")
    send_message(
        chat_id,
        f"✅ Import selesai: <b>{added}</b> ditambah, <b>{updated}</b> diperbarui.",
    )


# --------------------------------------------------------------------------- #
# Long-polling loop
# --------------------------------------------------------------------------- #


def get_updates(offset: int) -> list[dict]:
    res = _tg("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": []}, timeout=60)
    if not res.get("ok"):
        return []
    return res.get("result") or []


def main() -> int:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN is not set")
        return 1
    if not ALLOWED_USERS:
        log.warning("ALLOWED_USER_IDS is empty - bot is open to everyone!")
    if not REVEAL_PIN:
        log.warning("REVEAL_PIN is empty - API keys will be shown without PIN")

    me = _tg("getMe")
    if not me.get("ok"):
        log.error("Bot token invalid: %s", me)
        return 1
    log.info("Bot online: @%s", me["result"].get("username"))

    # Start clean: drop any stale updates from before this run.
    offset = 0
    try:
        stale = get_updates(offset)
        if stale:
            offset = max(u["update_id"] for u in stale) + 1
            log.info("Skipped %d stale update(s)", len(stale))
    except Exception:  # noqa: BLE001
        log.exception("failed to flush stale updates")

    deadline = float(os.environ.get("BOT_MAX_RUNTIME_SEC", "0") or 0)
    started = time.time()

    while True:
        if deadline and time.time() - started > deadline:
            log.info("Reached max runtime (%ds) - exiting for rotation", int(deadline))
            return EXIT_ROTATE
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                if "message" in u:
                    handle_message(u["message"])
                elif "callback_query" in u:
                    handle_callback(u["callback_query"])
        except KeyboardInterrupt:
            log.info("Interrupted by user")
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("polling error: %s", exc)
            time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())