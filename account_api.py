"""Read-only Mini App API. No imports of bot.py or shared SQLite cursors."""
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


class ApiError(RuntimeError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def validate_init_data(raw, token, now=None):
    if not isinstance(raw, str) or not raw or len(raw) > 16384:
        raise ValueError("invalid_init_data")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate_fields")
    signature = fields.pop("hash", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", signature):
        raise ValueError("invalid_hash")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise ValueError("invalid_signature")
    age = (time.time() if now is None else now) - int(fields.get("auth_date", "0"))
    if age < -30 or age > 3600:
        raise ValueError("expired_init_data")
    user = json.loads(fields.get("user", "null"))
    if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
        raise ValueError("invalid_user")
    return user


def safe_install_url(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\s\x00-\x1f]", value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return None


def delivery_data(message):
    """Only recognize explicit install links, never arbitrary supplier URLs/text."""
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    candidates = re.findall(r'https://[^\s<>"\u00ab\u00bb]+', text)
    for entity in (getattr(message, "entities", None) or []) + (getattr(message, "caption_entities", None) or []):
        if getattr(entity, "type", "") == "text_link":
            candidates.append(entity.url)
    links = {url for url in candidates if safe_install_url(url) and
             urlsplit(url).hostname == "esimsetup.apple.com" and
             urlsplit(url).path == "/esim_qrcode_provisioning" and
             dict(parse_qsl(urlsplit(url).query)).get("carddata", "").startswith("LPA:1$")}
    photo = getattr(message, "photo", None)
    document = getattr(message, "document", None)
    file_id = photo[-1].file_id if photo else None
    if not file_id and document and getattr(document, "mime_type", "") in ("image/png", "image/jpeg"):
        file_id = document.file_id
    return next(iter(links)) if len(links) == 1 else None, file_id


def read_account(db_path, user, referral_link, referral_share_url, referral_text, support_url):
    # mode=ro prevents accidental creation of a second/empty production database.
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        profile = db.execute("SELECT balance, username, first_name FROM users WHERE user_id=?", (user["id"],)).fetchone()
        referrals = db.execute("SELECT COUNT(*) FROM users WHERE ref=?", (user["id"],)).fetchone()[0]
        rows = db.execute("""
            SELECT id, country, tariff, esim_sent_at, install_confirmed, install_url, esim_file_id
            FROM orders WHERE user_id=? AND status='paid'
            ORDER BY CASE WHEN COALESCE(esim_sent_at, 0)>0 THEN 0 ELSE 1 END, id DESC
        """, (user["id"],)).fetchall()
    esims = []
    for row in rows:
        sent = bool(row["esim_sent_at"])
        esims.append({
            "id": row["id"], "country": row["country"], "plan_name": row["tariff"],
            "status": "issued" if sent else "preparing",
            "install_confirmed": bool(row["install_confirmed"]) if sent else False,
            "install_url": safe_install_url(row["install_url"]) if sent else None,
            "has_install_image": bool(sent and row["esim_file_id"]),
            "iccid": None, "provider_status": None, "traffic_remaining": None,
            "activated_at": None, "expires_at": None,
            "can_check_traffic": False, "can_top_up": False, "top_up_url": None,
        })
    return {"esims": esims, "profile": {
        "telegram_id": user["id"],
        "first_name": (profile["first_name"] if profile else None) or user.get("first_name", ""),
        "username": (profile["username"] if profile else None) or user.get("username", ""),
        "bonus_balance": profile["balance"] if profile else 0, "referrals_count": referrals,
        "referral_url": referral_link(user["id"]), "referral_share_url": referral_share_url(user["id"]),
        "referral_text": referral_text(), "support_url": support_url,
    }}


def create_account_server(host, port, token, read, read_image, create_payment=None,
                          read_payment=None, accept_webhook=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Never log initData, installation credentials, or request bodies.

        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

        def do_POST(self):
            image_match = re.fullmatch(r"/api/account/image/([1-9][0-9]*)", self.path)
            payment_match = re.fullmatch(r"/api/payments/status/([1-9][0-9]*)", self.path)
            is_payment_create = self.path == "/api/payments/create"
            is_webhook = self.path == "/api/payments/tochka/webhook"
            if self.path != "/api/account" and not image_match and not payment_match and not is_payment_create and not is_webhook:
                return self.reply(404, {"error": "not_found"})

            if is_webhook:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 50000 or self.headers.get("Transfer-Encoding"):
                        return self.reply(413, {"error": "invalid_body_size"})
                    token_body = self.rfile.read(length).decode("ascii").strip()
                    if accept_webhook is None:
                        raise ApiError(503, "payments_unavailable")
                    accept_webhook(token_body)
                    return self.reply(200, {"ok": True})
                except ApiError as exc:
                    return self.reply(exc.status, {"error": exc.code})
                except (UnicodeError, ValueError):
                    return self.reply(401, {"error": "invalid_webhook"})
                except Exception:
                    return self.reply(503, {"error": "webhook_unavailable"})

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20000 or self.headers.get("Transfer-Encoding"):
                    return self.reply(413, {"error": "invalid_body_size"})
                body = json.loads(self.rfile.read(length))
                user = validate_init_data(body.get("init_data") if isinstance(body, dict) else None, token)
            except (ValueError, TypeError, UnicodeError):
                return self.reply(401, {"error": "telegram_auth_required"})
            try:
                if image_match:
                    image = read_image(user["id"], int(image_match[1]))
                    if image is None:
                        return self.reply(404, {"error": "not_found"})
                    return self.reply(200, image[0], image[1])
                if is_payment_create:
                    if create_payment is None:
                        raise ApiError(503, "payments_unavailable")
                    return self.reply(200, create_payment(user, body))
                if payment_match:
                    if read_payment is None:
                        raise ApiError(503, "payments_unavailable")
                    return self.reply(200, read_payment(user, int(payment_match[1])))
                self.reply(200, read(user))
            except ApiError as exc:
                self.reply(exc.status, {"error": exc.code})
            except Exception:
                self.reply(503, {"error": "payments_unavailable" if is_payment_create or payment_match else "account_unavailable"})

    return ThreadingHTTPServer((host, port), Handler)


def start_account_api(host, port, token, read, read_image, create_payment=None,
                      read_payment=None, accept_webhook=None):
    server = create_account_server(
        host, port, token, read, read_image, create_payment, read_payment, accept_webhook
    )
    threading.Thread(target=server.serve_forever, daemon=True, name="account-api").start()
    return server
