"""Server-side client for the Banana partner eSIM API."""
import hashlib
import json
import os
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import certifi


STANDARD_PARTNER_PROVIDERS = frozenset({
    "supplier_standard",
    "supplier_alternative",
})


class BananaError(RuntimeError):
    def __init__(self, code, detail="", *, http_status=None, supplier_code=""):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.supplier_code = supplier_code


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
        # The partner documentation shows the root; older deployments already
        # configure the /standard suffix. Accept both without doubling it.
        if self.base_url.endswith("/v1"):
            self.base_url += "/standard"
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    @property
    def configured(self):
        return bool(self.key and self.site)

    def _headers(self):
        if not self.configured:
            raise BananaError("banana_not_configured")
        if any("\n" in value or "\r" in value for value in (self.key, self.site, self.client_name)):
            raise BananaError("banana_invalid_config")
        parsed_site = urlsplit(self.site if "://" in self.site else f"https://{self.site}")
        if (parsed_site.scheme != "https" or not parsed_site.hostname
                or parsed_site.username or parsed_site.password
                or any(char.isspace() for char in self.site)):
            raise BananaError("banana_invalid_site")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Partner-Key": self.key,
            "X-Partner-Site": self.site,
            "X-Partner-Client": self.client_name,
        }

    def _request(self, method, path, data=None, extra_headers=None, *, return_status=False):
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method=method)
        for name, value in self._headers().items():
            request.add_header(name, value)
        for name, value in (extra_headers or {}).items():
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                raise BananaError("banana_invalid_order")
            request.add_header(name, value)
        try:
            with urlopen(request, timeout=20, context=self._ssl_context) as response:
                response_status = response.status
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            detail = ""
            supplier_code = ""
            try:
                raw_error = exc.read(8192).decode("utf-8", errors="replace")
                error_data = json.loads(raw_error)
                if isinstance(error_data, dict):
                    supplier_code = str(error_data.get("code") or "")[:100]
                    detail = str(
                        error_data.get("message") or error_data.get("error")
                        or error_data.get("code") or raw_error
                    )
                else:
                    detail = raw_error
            except Exception:
                pass
            raise BananaError(
                f"banana_http_{exc.code}", detail[:300],
                http_status=exc.code, supplier_code=supplier_code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BananaError("banana_unavailable") from exc

        if not raw or len(raw) > 1024 * 1024:
            raise BananaError("banana_invalid_response", http_status=response_status)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BananaError("banana_invalid_response", http_status=response_status) from exc
        if not isinstance(result, (dict, list)):
            raise BananaError("banana_invalid_response", http_status=response_status)
        return (result, response_status) if return_status else result

    @staticmethod
    def _iccid(iccid):
        value = str(iccid or "").strip()
        if not re.fullmatch(r"[0-9]{15,22}", value):
            raise BananaError("banana_invalid_iccid")
        return value

    @classmethod
    def _validate_sim_card(cls, card, *, expected_iccid=None, installation=False):
        if not isinstance(card, dict):
            raise BananaError("banana_invalid_line_response")
        iccid = cls._iccid(card.get("iccid"))
        if expected_iccid is not None and iccid != expected_iccid:
            raise BananaError("banana_line_mismatch")
        if installation and not str(card.get("lpa_code") or "").startswith("LPA:1$"):
            raise BananaError("banana_missing_installation_data")
        # Missing balances are valid for some providers; retain them as unknown.
        # Never turn a partial response into a zero balance.
        for key in ("remaining_usage_kb", "allowed_usage_kb", "remaining_days"):
            value = card.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise BananaError("banana_invalid_line_response")
        return card

    def health(self):
        return self._request("GET", "/health")

    @staticmethod
    def request_id(order_id, item_id=1, provider="standard"):
        source = f"{order_id}/{item_id}/{provider}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    @staticmethod
    def _product_reference(product_id, variation_id):
        if isinstance(product_id, bool) or not isinstance(product_id, int) or product_id <= 0:
            raise BananaError("banana_invalid_product_reference")
        if isinstance(variation_id, bool) or not isinstance(variation_id, int) or variation_id < 0:
            raise BananaError("banana_invalid_product_reference")
        return {"product_id": product_id, "variation_id": variation_id}

    @staticmethod
    def _response_product_id(value, *, allow_zero=False):
        """Normalize WooCommerce IDs, which the live API may encode as strings."""
        if isinstance(value, bool):
            raise BananaError("banana_invalid_product_response")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            normalized = int(value)
        else:
            raise BananaError("banana_invalid_product_response")
        if normalized < 0 or (normalized == 0 and not allow_zero):
            raise BananaError("banana_invalid_product_response")
        return normalized

    @staticmethod
    def _response_bool(value):
        if isinstance(value, bool):
            return value
        if value in (0, "0"):
            return False
        if value in (1, "1"):
            return True
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "false":
                return False
            if lowered == "true":
                return True
        raise BananaError(
            "banana_invalid_product_response",
            f"invalid boolean field: {type(value).__name__}",
        )

    @staticmethod
    def _safe_error_detail(detail, fallback):
        text = str(detail or "")
        text = re.sub(r"[\r\n\t]+", " ", text)
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
        if (not text
                or "{" in text or "}" in text or "[" in text or "]" in text
                or re.search(
                    r"(?i)\b(?:headers?|authorization|bearer|api[_ -]?key|telegram[_ -]?token|"
                    r"x-partner-(?:key|site|client))\b",
                    text,
                )):
            return str(fallback or "banana_error")[:300]
        text = re.sub(r"(?i)LPA:1\$[^\s\"'<>]+", "[REDACTED_LPA]", text)
        text = re.sub(
            r"(?i)(\bactivation[_\s-]*code\b\s*(?::|=|is)?\s*)[\"']?[^\s,;]+",
            r"\1[REDACTED_ACTIVATION_CODE]",
            text,
        )
        text = re.sub(r"(?<!\d)\d{15,}(?!\d)", "[REDACTED_ICCID]", text)
        text = re.sub(r"\s+", " ", text).strip().replace('"', "'")
        return (text or str(fallback or "banana_error"))[:300]

    def resolve_product(self, product_id, variation_id):
        result = self._request(
            "POST",
            "/product/resolve",
            self._product_reference(product_id, variation_id),
        )
        if not isinstance(result, dict):
            raise BananaError("banana_invalid_product_response")
        resolved_product_id = self._response_product_id(result.get("product_id"))
        resolved_variation_id = self._response_product_id(
            result.get("variation_id"), allow_zero=True,
        )
        if resolved_product_id != product_id or resolved_variation_id != variation_id:
            raise BananaError(
                "banana_invalid_product_response",
                f"product reference mismatch: {resolved_product_id}/{resolved_variation_id}",
            )
        provider = result.get("partner_provider")
        if provider not in STANDARD_PARTNER_PROVIDERS | {"supplier_unlimited"}:
            raise BananaError(
                "banana_invalid_product_response",
                f"unsupported partner_provider: {str(provider)[:80]}",
            )
        raw_unlimited = result.get("unlimited")
        resolved_unlimited = (
            provider == "supplier_unlimited"
            if raw_unlimited is None else self._response_bool(raw_unlimited)
        )
        if resolved_unlimited != (provider == "supplier_unlimited"):
            raise BananaError(
                "banana_invalid_product_response",
                "partner_provider and unlimited disagree",
            )
        result["product_id"] = resolved_product_id
        result["variation_id"] = resolved_variation_id
        result["unlimited"] = resolved_unlimited
        if result.get("refillable") is not None:
            result["refillable"] = self._response_bool(result["refillable"])
        return result

    def create_line(self, order_id, product_id, variation_id, count=1, item_id=1):
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise BananaError("banana_invalid_order")
        payload = self._product_reference(product_id, variation_id)
        payload["count"] = count
        print(
            f"BANANA_CREATE_REQUEST order_id={order_id} product_id={product_id} "
            f"variation_id={variation_id} count={count}",
            flush=True,
        )
        http_status = "unknown"
        try:
            result, http_status = self._request(
                "POST",
                "/line/create",
                payload,
                {
                    "X-Partner-Request-ID": self.request_id(order_id, item_id),
                    "X-Partner-Order-ID": str(order_id),
                },
                return_status=True,
            )
            self._validate_sim_card(
                result.get("sim_card") if isinstance(result, dict) else None,
                installation=True,
            )
        except BananaError as exc:
            if isinstance(exc.http_status, int):
                http_status = exc.http_status
            supplier_code = re.sub(r"[^A-Za-z0-9_.:-]+", "_", exc.supplier_code or "")[:100]
            message = self._safe_error_detail(exc.detail, exc.code)
            print(
                f"BANANA_CREATE_ERROR order_id={order_id} product_id={product_id} "
                f"variation_id={variation_id} http_status={http_status} "
                f'supplier_code="{supplier_code}" message="{message}"',
                flush=True,
            )
            raise
        print(f"BANANA_CREATE_OK order_id={order_id} http_status={http_status}", flush=True)
        return result

    def get_details(self, iccid):
        value = self._iccid(iccid)
        result = self._request("GET", f"/line/{quote(value, safe='')}/get_details")
        self._validate_sim_card(result.get("sim_card") if isinstance(result, dict) else None, expected_iccid=value)
        return result

    def refill(
        self, order_id, iccid, product_id, variation_id,
        line_provider="supplier_standard", item_id=1,
    ):
        value = self._iccid(iccid)
        if line_provider not in STANDARD_PARTNER_PROVIDERS:
            raise BananaError("banana_invalid_line_provider")
        payload = self._product_reference(product_id, variation_id)
        payload["line_provider"] = line_provider
        result = self._request(
            "POST",
            f"/line/{quote(value, safe='')}/refill",
            payload,
            {
                "X-Partner-Request-ID": self.request_id(
                    order_id, item_id, f"topup-{value}-{variation_id}"
                )
            },
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise BananaError("banana_invalid_refill_response")
        if result.get("iccid") is not None and self._iccid(result["iccid"]) != value:
            raise BananaError("banana_line_mismatch")
        return result
