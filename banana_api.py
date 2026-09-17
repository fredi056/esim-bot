"""Server-side client for the Banana partner eSIM API."""
import hashlib
import json
import os
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import certifi


class BananaError(RuntimeError):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class BananaClient:
    def __init__(self):
        self.key = os.getenv("BANANA_PARTNER_KEY", "").strip()
        self.site = os.getenv("BANANA_PARTNER_SITE", "https://t.me/esimlimebot/").strip()
        self.client_name = os.getenv(
            "BANANA_PARTNER_CLIENT", "partner-esim-store-core/1.4.0"
        ).strip()
        self.base_url = os.getenv(
            "BANANA_API_BASE_URL",
            "https://esimbanana.com/wp-json/banana-supplier/v1/standard",
        ).rstrip("/")
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    @property
    def configured(self):
        return bool(self.key and self.site)

    def _headers(self):
        if not self.configured:
            raise BananaError("banana_not_configured")
        if any("\n" in value or "\r" in value for value in (self.key, self.site, self.client_name)):
            raise BananaError("banana_invalid_config")
        parsed_site = urlsplit(self.site)
        if parsed_site.scheme != "https" or not parsed_site.hostname:
            raise BananaError("banana_invalid_site")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Partner-Key": self.key,
            "X-Partner-Site": self.site,
            "X-Partner-Client": self.client_name,
        }

    def _request(self, method, path, data=None, extra_headers=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method=method)
        for name, value in self._headers().items():
            request.add_header(name, value)
        for name, value in (extra_headers or {}).items():
            request.add_header(name, value)
        try:
            with urlopen(request, timeout=20, context=self._ssl_context) as response:
                raw = response.read(1024 * 1024)
        except HTTPError as exc:
            detail = ""
            try:
                raw_error = exc.read(8192).decode("utf-8", errors="replace")
                error_data = json.loads(raw_error)
                if isinstance(error_data, dict):
                    detail = str(
                        error_data.get("message") or error_data.get("error")
                        or error_data.get("code") or raw_error
                    )
                else:
                    detail = raw_error
            except Exception:
                pass
            raise BananaError(f"banana_http_{exc.code}", detail[:300]) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BananaError("banana_unavailable") from exc

        if not raw:
            return {}
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BananaError("banana_invalid_response") from exc
        if not isinstance(result, (dict, list)):
            raise BananaError("banana_invalid_response")
        return result

    def health(self):
        return self._request("GET", "/health")

    @staticmethod
    def request_id(order_id, item_id=1, provider="standard"):
        source = f"{order_id}/{item_id}/{provider}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    def create_line(self, order_id, bundle_id, refill_mb, refill_days, count=1, item_id=1):
        values = (bundle_id, refill_mb, refill_days, count)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise BananaError("banana_invalid_order")
        return self._request(
            "POST",
            "/line/create",
            {
                "bundle_id": bundle_id,
                "refill_mb": refill_mb,
                "refill_days": refill_days,
                "count": count,
            },
            {
                "X-Partner-Request-ID": self.request_id(order_id, item_id),
                "X-Partner-Order-ID": str(order_id),
            },
        )

    def get_details(self, iccid):
        value = str(iccid or "").strip()
        if not value.isdigit() or not 15 <= len(value) <= 22:
            raise BananaError("banana_invalid_iccid")
        return self._request("GET", f"/line/{quote(value, safe='')}/get_details")

    def refill(self, iccid, amount_mb, amount_days):
        value = str(iccid or "").strip()
        if not value.isdigit() or not 15 <= len(value) <= 22:
            raise BananaError("banana_invalid_iccid")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in (amount_mb, amount_days)
        ):
            raise BananaError("banana_invalid_refill")
        return self._request(
            "POST",
            f"/line/{quote(value, safe='')}/refill",
            {"amount_mb": amount_mb, "amount_days": amount_days},
        )
