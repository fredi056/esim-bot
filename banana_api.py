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


class BananaError(RuntimeError):
    def __init__(self, code, detail="", *, http_status=None, supplier_code=""):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.supplier_code = supplier_code


class BananaClient:
    CREATE_REQUEST_ID_VERSION = "standard-v2"
    REFILL_REQUEST_ID_VERSION = "topup-v2"

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
    def _unwrap_response(result):
        if not isinstance(result, dict):
            raise BananaError("banana_invalid_response")
        is_wrapper = any(key in result for key in ("obj", "errorCode", "errorMsg"))
        if not is_wrapper:
            return result
        success = result.get("success")
        if success is False or success == 0:
            supplier_code = str(result.get("errorCode") or "").strip()
            raise BananaError(
                supplier_code or "banana_supplier_error",
                str(result.get("errorMsg") or ""),
                supplier_code=supplier_code,
            )
        if success is not True and success != 1:
            raise BananaError("banana_invalid_response")
        payload = result.get("obj")
        if not isinstance(payload, dict):
            raise BananaError("banana_invalid_response")
        return payload

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
        return self._unwrap_response(self._request("GET", "/health"))

    @staticmethod
    def request_id(order_id, item_id=1, provider="standard"):
        source = f"{order_id}/{item_id}/{provider}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    @staticmethod
    def _item_id(item_id):
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
            raise BananaError("banana_invalid_product_reference")
        return item_id

    @staticmethod
    def _period_days(period_days):
        if isinstance(period_days, bool) or not isinstance(period_days, int) or period_days <= 0:
            raise BananaError("banana_invalid_period")
        return period_days

    @staticmethod
    def _request_reference(request_id):
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise BananaError("banana_invalid_request_id")
        raw_value = str(request_id)
        value = raw_value.strip()
        if not raw_value.isprintable() or not value or len(value) > 500:
            raise BananaError("banana_invalid_request_id")
        return value

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

    @staticmethod
    def _safe_log_key(key):
        raw = str(key)
        if (
            re.search(r"(?<!\d)\d{15,}(?!\d)", raw)
            or "LPA:1$" in raw.upper()
            or re.search(r"(?i)https?://", raw)
        ):
            raw = "redacted_key"
        return re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)[:80] or "empty_key"

    @classmethod
    def _safe_log_keys(cls, value):
        if not isinstance(value, dict):
            return "none"
        keys = [cls._safe_log_key(key) for key in value.keys()]
        return ",".join(sorted(keys))[:500] or "none"

    @classmethod
    def _safe_log_field_types(cls, value):
        if not isinstance(value, dict):
            return "none"
        fields = [
            f"{cls._safe_log_key(key)}:{type(field).__name__}"
            for key, field in value.items()
        ]
        return ",".join(sorted(fields))[:1000] or "none"

    @classmethod
    def _safe_log_nested_shapes(cls, value):
        if not isinstance(value, dict):
            return "none"
        shapes = []
        for key, field in value.items():
            safe_key = cls._safe_log_key(key)
            if isinstance(field, dict):
                shapes.append(f"{safe_key}:dict(keys={cls._safe_log_keys(field)})")
            elif isinstance(field, list):
                first = field[0] if field else None
                first_keys = cls._safe_log_keys(first) if isinstance(first, dict) else "none"
                shapes.append(
                    f"{safe_key}:list(count={len(field)},first_type={type(first).__name__ if field else 'missing'},"
                    f"first_keys={first_keys})"
                )
        return ";".join(sorted(shapes))[:1500] or "none"

    @classmethod
    def _log_details_response(cls, result):
        if isinstance(result, dict):
            sim_card_present = "sim_card" in result
            card = result.get("sim_card") if sim_card_present else None
            card_is_dict = isinstance(card, dict)
            iccid_present = card_is_dict and "iccid" in card
            iccid_length = len(str(card.get("iccid") or "").strip()) if iccid_present else 0
            esim_list_present = "esimList" in result
            esim_list = result.get("esimList") if esim_list_present else None
            esim_list_is_list = isinstance(esim_list, list)
            esim_first = esim_list[0] if esim_list_is_list and esim_list else None
            esim_first_is_dict = isinstance(esim_first, dict)

            def field_type(key):
                return type(card[key]).__name__ if card_is_dict and key in card else "missing"

            print(
                f"BANANA_DETAILS_RESPONSE response_type=dict keys={cls._safe_log_keys(result)} "
                f"sim_card_present={str(sim_card_present).lower()} "
                f"sim_card_type={type(card).__name__ if sim_card_present else 'missing'} "
                f"sim_card_keys={cls._safe_log_keys(card) if card_is_dict else 'none'} "
                f"iccid_present={str(iccid_present).lower()} iccid_length={iccid_length} "
                f"remaining_usage_type={field_type('remaining_usage_kb')} "
                f"allowed_usage_type={field_type('allowed_usage_kb')} "
                f"remaining_days_type={field_type('remaining_days')} "
                f"status_present={str(card_is_dict and 'status' in card).lower()} "
                f"refillable_type={field_type('refillable')} "
                f"esimList_present={str(esim_list_present).lower()} "
                f"esimList_type={type(esim_list).__name__ if esim_list_present else 'missing'} "
                f"esimList_count={len(esim_list) if esim_list_is_list else 0} "
                f"esimList_first_type={type(esim_first).__name__ if esim_list_is_list and esim_list else 'missing'} "
                f"esimList_first_keys={cls._safe_log_keys(esim_first) if esim_first_is_dict else 'none'} "
                f"esimList_first_field_types={cls._safe_log_field_types(esim_first) if esim_first_is_dict else 'none'} "
                f"esimList_first_nested={cls._safe_log_nested_shapes(esim_first) if esim_first_is_dict else 'none'}",
                flush=True,
            )
            return
        if isinstance(result, list):
            print(
                f"BANANA_DETAILS_RESPONSE response_type=list count={len(result)}",
                flush=True,
            )
            return
        print(
            f"BANANA_DETAILS_RESPONSE response_type={type(result).__name__}",
            flush=True,
        )

    @classmethod
    def _log_refill_response(cls, result, http_status="unknown"):
        status = http_status if isinstance(http_status, int) else "unknown"
        if isinstance(result, dict):
            print(
                f"BANANA_REFILL_RESPONSE http_status={status} response_type=dict "
                f"keys={cls._safe_log_keys(result)} "
                f"field_types={cls._safe_log_field_types(result)} "
                f"nested={cls._safe_log_nested_shapes(result)}",
                flush=True,
            )
            return
        if isinstance(result, list):
            first = result[0] if result else None
            first_is_dict = isinstance(first, dict)
            print(
                f"BANANA_REFILL_RESPONSE http_status={status} response_type=list "
                f"count={len(result)} first_type={type(first).__name__ if result else 'missing'} "
                f"first_keys={cls._safe_log_keys(first) if first_is_dict else 'none'} "
                f"first_field_types={cls._safe_log_field_types(first) if first_is_dict else 'none'} "
                f"first_nested={cls._safe_log_nested_shapes(first) if first_is_dict else 'none'}",
                flush=True,
            )
            return
        print(
            f"BANANA_REFILL_RESPONSE http_status={status} response_type={type(result).__name__}",
            flush=True,
        )

    @staticmethod
    def _refill_invalid_reason(result):
        if not isinstance(result, dict):
            return "unexpected_response_shape"
        if "success" not in result:
            return "missing_success"
        if not isinstance(result["success"], bool):
            return "success_wrong_type"
        if result["success"] is False:
            return "success_false"
        return "unknown_validation_error"

    @classmethod
    def _details_invalid_reason(cls, result, expected_iccid):
        if not isinstance(result, dict) or "sim_card" not in result:
            return "missing_sim_card"
        card = result.get("sim_card")
        if not isinstance(card, dict):
            return "invalid_sim_card_type"
        raw_iccid = card.get("iccid")
        if raw_iccid is None or not str(raw_iccid).strip():
            return "missing_iccid"
        try:
            normalized_iccid = cls._iccid(raw_iccid)
        except BananaError:
            return "invalid_iccid"
        if normalized_iccid != expected_iccid:
            return "iccid_mismatch"
        for key, reason in (
            ("remaining_usage_kb", "invalid_remaining_usage_type"),
            ("allowed_usage_kb", "invalid_allowed_usage_type"),
            ("remaining_days", "invalid_remaining_days_type"),
        ):
            field = card.get(key)
            if field is not None and (
                isinstance(field, bool) or not isinstance(field, int) or field < 0
            ):
                return reason
        return "unknown_validation_error"

    def create_line(self, order_id, item_id, count=1, period_days=None):
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise BananaError("banana_invalid_order")
        item_id = self._item_id(item_id)
        payload = {"item_id": item_id, "count": count}
        if period_days is not None:
            payload["period_days"] = self._period_days(period_days)
        print(
            f"BANANA_CREATE_REQUEST order_id={order_id} item_id={item_id} count={count}",
            flush=True,
        )
        http_status = "unknown"
        try:
            result, http_status = self._request(
                "POST",
                "/line/create",
                payload,
                {
                    "X-Partner-Request-ID": self.request_id(
                        order_id, item_id, self.CREATE_REQUEST_ID_VERSION
                    ),
                    "X-Partner-Order-ID": str(order_id),
                },
                return_status=True,
            )
            result = self._unwrap_response(result)
            if "sim_card" not in result and all(result.get(key) for key in ("iccid", "lpa_code")):
                result = {"sim_card": result}
            if isinstance(result.get("sim_card"), dict):
                self._validate_sim_card(result["sim_card"], installation=True)
            else:
                raw_request_id = result.get("request_id")
                response_keys = ",".join(sorted(
                    re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(key))[:80]
                    for key in result.keys()
                ))[:500] or "none"
                request_id_present = "request_id" in result and raw_request_id is not None
                request_id_length = (
                    len(str(raw_request_id).strip()) if request_id_present else 0
                )
                request_id_type = type(raw_request_id).__name__
                response_status = self._safe_error_detail(result.get("status"), "missing")
                print(
                    f"BANANA_CREATE_RESPONSE order_id={order_id} http_status={http_status} "
                    f"keys={response_keys} status={response_status} "
                    f"request_id_present={str(request_id_present).lower()} "
                    f"request_id_type={request_id_type} request_id_length={request_id_length}",
                    flush=True,
                )
                request_id = self._request_reference(raw_request_id)
                if result.get("status") != "processing":
                    raise BananaError("banana_invalid_line_response", http_status=http_status)
                result = {"request_id": request_id, "status": "processing"}
        except BananaError as exc:
            if isinstance(exc.http_status, int):
                http_status = exc.http_status
            supplier_code = re.sub(r"[^A-Za-z0-9_.:-]+", "_", exc.supplier_code or "")[:100]
            message = self._safe_error_detail(exc.detail, exc.code)
            print(
                f"BANANA_CREATE_ERROR order_id={order_id} item_id={item_id} http_status={http_status} "
                f'supplier_code="{supplier_code}" message="{message}"',
                flush=True,
            )
            raise
        print(f"BANANA_CREATE_OK order_id={order_id} http_status={http_status}", flush=True)
        if result.get("status") == "processing":
            print(
                f"BANANA_CREATE_ASYNC order_id={order_id} request_id_present=true "
                f"request_id_length={len(result['request_id'])}",
                flush=True,
            )
        return result

    def get_request(self, order_id, request_id):
        request_id = self._request_reference(request_id)
        result = self._unwrap_response(
            self._request("GET", f"/request/{quote(request_id, safe='')}")
        )
        status = str(result.get("status") or "").strip().lower()
        print(f"BANANA_REQUEST_STATUS order_id={order_id} status={status or 'unknown'}", flush=True)
        if status == "processing":
            return {"request_id": request_id, "status": "processing"}
        if status == "completed":
            response_result = result.get("result")
            cards = response_result.get("sim_cards") if isinstance(response_result, dict) else None
            if not isinstance(cards, list) or len(cards) != 1:
                raise BananaError("banana_invalid_request_response")
            card = self._validate_sim_card(cards[0], installation=True)
            return {"request_id": request_id, "status": "completed", "sim_card": card}
        if status == "failed":
            detail = result.get("message") or result.get("error") or result.get("code") or ""
            raise BananaError(
                "banana_async_failed", str(detail)[:300],
                supplier_code=str(result.get("code") or "")[:100],
            )
        raise BananaError("banana_invalid_request_response")

    def get_details(self, iccid):
        value = self._iccid(iccid)
        raw_result = self._request("GET", f"/line/{quote(value, safe='')}/get_details")
        try:
            result = self._unwrap_response(raw_result)
        except BananaError:
            self._log_details_response(raw_result)
            raise
        self._log_details_response(result)
        reason = self._details_invalid_reason(result, value)
        try:
            self._validate_sim_card(
                result.get("sim_card") if isinstance(result, dict) else None,
                expected_iccid=value,
            )
        except BananaError:
            print(f"BANANA_DETAILS_INVALID reason={reason}", flush=True)
            raise
        return result

    def refill(self, order_id, iccid, item_id, period_days=None):
        value = self._iccid(iccid)
        item_id = self._item_id(item_id)
        payload = {"item_id": item_id}
        if period_days is not None:
            payload["period_days"] = self._period_days(period_days)
        response = self._request(
            "POST",
            f"/line/{quote(value, safe='')}/refill",
            payload,
            {
                "X-Partner-Request-ID": self.request_id(
                    order_id, item_id, self.REFILL_REQUEST_ID_VERSION
                )
            },
            return_status=True,
        )
        if (
            isinstance(response, tuple) and len(response) == 2
            and isinstance(response[1], int)
        ):
            raw_result, http_status = response
        else:
            # Keep compatibility with injected test clients that predate
            # return_status while production always returns the tuple.
            raw_result, http_status = response, "unknown"
        try:
            result = self._unwrap_response(raw_result)
        except BananaError as exc:
            self._log_refill_response(raw_result, http_status)
            if exc.code == "banana_invalid_response":
                print(
                    "BANANA_REFILL_INVALID reason=unexpected_response_shape",
                    flush=True,
                )
                raise BananaError(
                    "banana_invalid_refill_response",
                    http_status=http_status if isinstance(http_status, int) else None,
                ) from exc
            raise
        self._log_refill_response(result, http_status)
        reason = self._refill_invalid_reason(result)
        if result.get("success") is not True:
            print(f"BANANA_REFILL_INVALID reason={reason}", flush=True)
            raise BananaError("banana_invalid_refill_response")
        if result.get("iccid") is not None and self._iccid(result["iccid"]) != value:
            raise BananaError("banana_line_mismatch")
        return result
