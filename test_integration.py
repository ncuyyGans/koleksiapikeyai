"""Integration test: drives bot.py's handlers in-process against a temp encrypted DB.

Simulates a real Telegram session: /add flow, /list, PIN-gated reveal, edit,
search, pin, delete. No network needed - we monkeypatch bot._tg.
"""
import os
import tempfile

# Isolated encrypted DB for this test run.
_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "data.db.enc")
os.environ["DB_ENCRYPTION_KEY"] = "jc4pl4Ix-saLKUTX4rfAwm60iNyU2oHdlnhtxj9sWKw="
os.environ["REVEAL_PIN"] = "677175"
os.environ["ALLOWED_USER_IDS"] = "1215200606"
os.environ["BOT_TOKEN"] = "test-token"

import bot  # noqa: E402
import db  # noqa: E402

CHAT = 1215200606
sent: list[tuple[str, dict | None]] = []


def fake_tg(method, payload=None, files=None, timeout=60):
    if method == "getMe":
        return {"ok": True, "result": {"username": "testbot"}}
    if method == "getUpdates":
        return {"ok": True, "result": []}
    if method in ("sendMessage", "sendDocument", "editMessageText", "deleteMessage"):
        sent.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}
    if method == "answerCallbackQuery":
        return {"ok": True, "result": True}
    return {"ok": True, "result": {}}


bot._tg = fake_tg

failures = []


def check(cond, label):
    if cond:
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label}")


def last_text():
    for method, p in reversed(sent):
        if method == "sendMessage" and p and p.get("text"):
            return p["text"]
    return ""


def last_markup():
    """Reply markup of the most recent sendMessage (inline keyboards live here,
    not in the message text)."""
    for method, p in reversed(sent):
        if method == "sendMessage" and p:
            return p.get("reply_markup") or {}
    return {}


def msg(text):
    bot.handle_message(
        {"chat": {"id": CHAT}, "from": {"id": CHAT}, "text": text}
    )


def cb(data):
    bot.handle_callback(
        {
            "id": "1",
            "data": data,
            "from": {"id": CHAT},
            "message": {"chat": {"id": CHAT}, "message_id": 1},
        }
    )


print("== 1. /start ==")
msg("/start")
check("Koleksi API Key AI" in last_text(), "/start shows welcome")

print("== 2. /add flow ==")
msg("/add deepseek-v3")
for answer in ["DeepSeek", "https://api.deepseek.com/v1", "sk-abc123456789xyz", "deepseek-chat"]:
    msg(answer)
msg("/skip")  # notes
msg("/skip")  # tags
check("Ditambahkan" in last_text(), "/add completes")
models = db.list_models()
check(len(models) == 1, "one model stored")
check(models[0]["provider"] == "DeepSeek", "provider saved")
check(models[0]["api_key"] == "sk-abc123456789xyz", "api_key saved")

print("== 3. /list masking ==")
msg("/list")
check("sk-a••••9xyz" in last_text(), "api key masked in /list")
check("<b>1. deepseek-v3</b>" in last_text(), "model name listed")

print("== 4. /view + copy without/with PIN ==")
msg("/view deepseek-v3")
check("Salin API Key" in str(last_markup()), "/view shows copy buttons")
cb("copy:api_key:deepseek-v3")
check("Ketik PIN" in last_text(), "PIN requested for api_key")
msg("000000")  # wrong pin
check("PIN salah" in last_text(), "wrong PIN rejected")
cb("copy:api_key:deepseek-v3")
msg("677175")  # correct pin
check("sk-abc123456789xyz" in last_text(), "correct PIN reveals key")
# Base URL needs no PIN
cb("copy:base_url:deepseek-v3")
check("https://api.deepseek.com/v1" in last_text(), "base_url copied without PIN")

print("== 5. /search ==")
msg("/search deepseek")
check("1 model" in last_text(), "/search finds match")
msg("/search nothingmatches")
check("Tidak ada hasil" in last_text(), "/search handles no match")

print("== 6. /edit ==")
msg("/edit deepseek-v3 api_key sk-NEWKEY987654")
check("diperbarui" in last_text(), "/edit works")
check(db.get_model("deepseek-v3")["api_key"] == "sk-NEWKEY987654", "edit persisted")

print("== 7. /pin ==")
msg("/pin deepseek-v3")
check("Disematkan" in last_text(), "/pin works")
check(db.get_model("deepseek-v3")["pinned"] == 1, "pin persisted")

print("== 8. HTML escaping ==")
msg("/add <weird>&name")
msg("A&B")
msg("http://x/<y>")
msg("<key>")
msg("/skip")
msg("/skip")
msg("/skip")  # tags -> 6 answers for the 6 add-flow steps
check("&lt;weird&gt;&amp;name" in last_text(), "escapes entities")
msg("/list")
check("&lt;weird&gt;&amp;name" in last_text(), "escaping visible in list")

print("== 9. /delete with confirm callback ==")
msg("/delete deepseek-v3")
check("Hapus" in last_text(), "delete asks confirmation")
cb("confirm_del:deepseek-v3")
check(db.get_model("deepseek-v3") is None, "delete persisted")

print("== 10. access control ==")
bot.handle_message({"chat": {"id": 999}, "from": {"id": 999}, "text": "/list"})
check("tidak diizinkan" in last_text().lower(), "unauthorized user rejected")

print("== 11. audit log ==")
db.add_audit(CHAT, "add", "x")
check(len(db.list_audit()) > 0, "audit rows exist")

print()
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    raise SystemExit(1)
print("RESULT: ALL INTEGRATION TESTS PASSED")
