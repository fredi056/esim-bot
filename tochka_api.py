"""Small server-side client for Tochka internet acquiring."""
import json
import os
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
import jwt


class TochkaError(RuntimeError):
    pass


class TochkaClient:
    def __init__(self):
        self.token = os.getenv("TOCHKA_API_TOKEN", "").strip()
        self.client_id = os.getenv("TOCHKA_CLIENT_ID", "").strip()
        self.base_url = os.getenv("TOCHKA_API_BASE_URL", "https://enter.tochka.com/uapi").rstrip("/")
        self.customer_code = os.getenv("TOCHKA_CUSTOMER_CODE", "").strip()
        self.merchant_id = os.getenv("TOCHKA_MERCHANT_ID", "").strip()
        self.payment_modes = None
        self._lock = threading.Lock()
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._public_key = None
        self._public_key_loaded_at = 0

    @property
    def configured(self):
        return bool(self.token and self.client_id)

    def _request(self, method, path, data=None, query=None):
        if not self.configured:
            raise TochkaError("tochka_not_configured")
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=12, context=self._ssl_context) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Do not propagate a response that could contain credentials or personal data.
            raise TochkaError(f"tochka_http_{exc.code}") from exc
        except (URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise TochkaError("tochka_unavailable") from exc
        if not isinstance(result, dict):
            raise TochkaError("tochka_invalid_response")
        return result

    def resolve_customer_code(self):
        if self.customer_code:
            return self.customer_code
        with self._lock:
            if self.customer_code:
                return self.customer_code
            response = self._request("GET", "/open-banking/v1.0/customers")
            customers = response.get("Data", {}).get("Customer", [])
            businesses = [item for item in customers if item.get("customerType") == "Business"]
            if len(businesses) != 1 or not businesses[0].get("customerCode"):
                raise TochkaError("tochka_customer_ambiguous")
            self.customer_code = businesses[0]["customerCode"]
            return self.customer_code

    def resolve_merchant_id(self, customer_code):
        if self.merchant_id:
            return self.merchant_id
        with self._lock:
            if self.merchant_id:
                return self.merchant_id
            response = self._request(
                "GET", "/acquiring/v1.0/retailers", query={"customerCode": customer_code}
            )
            retailers = response.get("Data", {}).get("Retailer", [])
            active = [
                item for item in retailers
                if item.get("status") == "REG" and item.get("isActive") is True and item.get("merchantId")
            ]
            if len(active) != 1:
                raise TochkaError("tochka_retailer_ambiguous")
            self.merchant_id = active[0]["merchantId"]
            available_modes = active[0].get("paymentModes") or []
            self.payment_modes = [mode for mode in ("sbp", "card") if mode in available_modes]
            if not self.payment_modes:
                raise TochkaError("tochka_payment_modes_unavailable")
            return self.merchant_id

    def create_payment(self, order_id, amount, purpose, redirect_url, fail_redirect_url):
        customer_code = self.resolve_customer_code()
        merchant_id = self.resolve_merchant_id(customer_code)
        payload = {
            "Data": {
                "customerCode": customer_code,
                "amount": amount,
                "purpose": purpose[:140],
                "redirectUrl": redirect_url,
                "failRedirectUrl": fail_redirect_url,
                "paymentMode": self.payment_modes or ["sbp"],
                "saveCard": False,
                "merchantId": merchant_id,
                "preAuthorization": False,
                "ttl": 1440,
                "paymentLinkId": f"esimlime-{order_id}",
            }
        }
        data = self._request("POST", "/acquiring/v1.0/payments", payload).get("Data", {})
        if not data.get("operationId") or not data.get("paymentLink"):
            raise TochkaError("tochka_invalid_payment_response")
        return data

    def get_payment(self, operation_id):
        if not isinstance(operation_id, str) or not operation_id:
            raise TochkaError("invalid_operation_id")
        return self._request("GET", f"/acquiring/v1.0/payments/{operation_id}").get("Data", {})

    def ensure_webhook(self, url):
        if not isinstance(url, str) or not url.startswith("https://"):
            raise TochkaError("invalid_webhook_url")
        path = f"/webhook/v1.0/{self.client_id}"
        try:
            current = self._request("GET", path).get("Data", {})
        except TochkaError as exc:
            if str(exc) != "tochka_http_404":
                raise
            current = None
        if current:
            current_url = current.get("url")
            if current_url != url:
                raise TochkaError("tochka_webhook_url_conflict")
            events = list(dict.fromkeys((current.get("webhooksList") or []) + ["acquiringInternetPayment"]))
            if events != (current.get("webhooksList") or []):
                self._request("POST", path, {"webhooksList": events, "url": url})
            return
        self._request("PUT", path, {"webhooksList": ["acquiringInternetPayment"], "url": url})

    def _load_public_key(self):
        now = time.time()
        if self._public_key is not None and now - self._public_key_loaded_at < 6 * 60 * 60:
            return self._public_key
        request = Request("https://enter.tochka.com/doc/openapi/static/keys/public", method="GET")
        request.add_header("Accept", "application/json")
        try:
            with urlopen(request, timeout=10, context=self._ssl_context) as response:
                jwk = json.loads(response.read().decode("utf-8"))
            key = jwt.PyJWK.from_dict(jwk).key
        except Exception as exc:
            raise TochkaError("tochka_public_key_unavailable") from exc
        self._public_key = key
        self._public_key_loaded_at = now
        return key

    def decode_webhook(self, token):
        if not isinstance(token, str) or not token or len(token) > 50000:
            raise TochkaError("invalid_webhook")
        try:
            payload = jwt.decode(
                token,
                self._load_public_key(),
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise TochkaError("invalid_webhook_signature") from exc
        audience = payload.get("aud")
        if audience and self.client_id not in ([audience] if isinstance(audience, str) else audience):
            raise TochkaError("invalid_webhook_audience")
        return payload
