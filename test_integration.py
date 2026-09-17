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

print("== 12. /export + /import round-trip ==")
import io  # noqa: E402
import urllib.request  # noqa: E402

last_export = {"bytes": None}


def fake_file(method, payload=None, files=None, timeout=60):
    # getFile must hand back a path; the export bytes are captured on send.
    if method == "getFile":
        return {"ok": True, "result": {"file_path": "exports/models.json"}}
    if method == "sendDocument" and files:
        doc = files.get("document")
        if doc:
            last_export["bytes"] = doc[1]
    return fake_tg(method, payload, files, timeout)


bot._tg = fake_file


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = lambda url, timeout=60: _FakeResp(last_export["bytes"] or b"{}")

# Export every model currently stored.
before = {m["name"]: m for m in db.list_models()}
msg("/export")
check(last_export["bytes"] is not None, "/export produces a JSON document")

# Wipe the DB, then restore it purely from the exported bytes via /import.
for name in list(before):
    db.delete_model(name)
check(len(db.list_models()) == 0, "DB emptied before import")

msg_with_doc = {
    "chat": {"id": CHAT},
    "from": {"id": CHAT},
    "text": "/import",
    "document": {"file_id": "abc", "file_name": "models.json"},
}
bot.handle_message(msg_with_doc)
check("Import selesai" in last_text(), "/import restored from exported file")
after = {m["name"]: m for m in db.list_models()}
check(set(before) == set(after), "imported model names match exported set")
for name, m in before.items():
    check(after[name]["api_key"] == m["api_key"], f"api_key round-tripped: {name}")

urllib.request.urlopen = _real_urlopen
bot._tg = fake_tg
sent.clear()

# --------------------------------------------------------------------------- #
# 13. Strict Telegram HTML validation of every message the bot produced.
#
# Earlier tests monkeypatched bot._tg, which happily accepted invalid HTML.
# Telegram's parse_mode=HTML rejects it at runtime with HTTP 400, so we replay
# every sendMessage payload through a validator modelled on Telegram's real
# rules. This catches unescaped placeholders (e.g. "<nama>"), unbalanced tags
# and stray "&" that a fake transport would never notice.
# --------------------------------------------------------------------------- #
import html.parser  # noqa: E402
import re  # noqa: E402

# Tags Telegram actually supports in parse_mode=HTML.
TELEGRAM_TAGS = {
    "b", "i", "u", "s", "code", "pre", "a", "span", "tg-spoiler",
    "tg-emoji", "blockquote", "expandableblockquote", "br",
}
VOID_TAGS = {"br"}
_ENTITY_RE = re.compile(r"&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")


class _HtmlChecker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in TELEGRAM_TAGS:
            self.errors.append(f"Unsupported start tag <{tag}>")
        elif tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag not in TELEGRAM_TAGS:
            self.errors.append(f"Unsupported start tag <{tag}>")

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            self.errors.append(f"Unexpected end tag </{tag}>")
            return
        if tag not in TELEGRAM_TAGS:
            self.errors.append(f"Unsupported end tag </{tag}>")
            return
        if tag not in self.stack:
            self.errors.append(f"Unexpected end tag </{tag}>")
            return
        # Pop down to the matching tag; anything in between was unbalanced.
        while self.stack:
            top = self.stack.pop()
            if top == tag:
                break
            self.errors.append(f"Unbalanced tag <{top}>")


def validate_telegram_html(text: str) -> list[str]:
    problems: list[str] = []
    checker = _HtmlChecker()
    try:
        checker.feed(text)
        checker.close()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"malformed markup: {exc}")
    problems.extend(checker.errors)
    if checker.stack:
        problems.append("Unclosed tag(s): " + ", ".join(f"<{t}>" for t in checker.stack))
    # Every "&" must be part of a well-formed entity reference.
    for i, ch in enumerate(text):
        if ch == "&" and not _ENTITY_RE.match(text, i):
            problems.append(f"Unescaped '&' at offset {i}")
    return problems


print("== 13. Telegram HTML validity of all bot messages ==")
# Exercise every text-producing command (including error branches) so the
# validator sees as much of the bot's output as possible.
sent.clear()
for command in [
    "/start", "/help", "/list", "/audit", "/stats", "/export",
    "/add", "/add deepseek-v3", "/view", "/view deepseek-v3",
    "/search", "/search deepseek", "/edit", "/edit x y z",
    "/delete", "/pin", "/import", "/cancel",
]:
    msg(command)
msg("/cancel")  # leave any pending add flow clean

html_failures = []
checked = 0
for method, p in sent:
    if method != "sendMessage" or not p:
        continue
    text = p.get("text") or ""
    checked += 1
    problems = validate_telegram_html(text)
    if problems:
        preview = text[:70].replace("\n", "\\n")
        html_failures.append(f"{preview!r} -> {'; '.join(problems)}")

check(checked > 0, "at least one message was validated")
check(not html_failures, "all messages are valid Telegram HTML")
if html_failures:
    for f in html_failures:
        print(f"    INVALID: {f}")

print()
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
    raise SystemExit(1)
print("RESULT: ALL INTEGRATION TESTS PASSED")
