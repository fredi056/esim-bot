"""Safe diagnostics for Banana get_details response variants."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock

from banana_api import BananaClient, BananaError


class BananaDetailsDiagnosticsTest(unittest.TestCase):
    ICCID = "8985201234567890123"
    LPA_CODE = "LPA:1$rsp.example$very-secret-code"
    ACTIVATION_CODE = "activation-secret-123"

    def client_with_response(self, response):
        client = BananaClient()
        client._request = Mock(return_value=response)
        return client

    def test_documented_sim_card_response_passes(self):
        response = {
            "sim_card": {
                "iccid": self.ICCID,
                "remaining_usage_kb": 1024,
                "allowed_usage_kb": 2048,
                "remaining_days": 30,
                "status": "active",
                "refillable": True,
            }
        }
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(result, response)
        logged = output.getvalue()
        self.assertIn("BANANA_DETAILS_RESPONSE", logged)
        self.assertIn("sim_card_present=true", logged)
        self.assertIn("sim_card_type=dict", logged)
        self.assertIn("remaining_usage_type=int", logged)

    def test_integer_numeric_values_pass(self):
        response = {
            "sim_card": {
                "iccid": self.ICCID,
                "remaining_usage_kb": 0,
                "allowed_usage_kb": 10,
                "remaining_days": 0,
            }
        }
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                self.client_with_response(response).get_details(self.ICCID),
                response,
            )

    def test_numeric_string_is_invalid_with_specific_reason(self):
        response = {
            "sim_card": {
                "iccid": self.ICCID,
                "remaining_usage_kb": "1024",
                "allowed_usage_kb": 2048,
                "remaining_days": 30,
            }
        }
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(caught.exception.code, "banana_invalid_line_response")
        self.assertIn("remaining_usage_type=str", output.getvalue())
        self.assertIn(
            "BANANA_DETAILS_INVALID reason=invalid_remaining_usage_type",
            output.getvalue(),
        )

    def test_missing_sim_card_has_specific_reason(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            self.client_with_response({"status": "active"}).get_details(self.ICCID)
        self.assertEqual(caught.exception.code, "banana_invalid_line_response")
        self.assertIn("sim_card_present=false", output.getvalue())
        self.assertIn("BANANA_DETAILS_INVALID reason=missing_sim_card", output.getvalue())

    def test_top_level_list_is_logged_without_values(self):
        response = [{
            "iccid": self.ICCID,
            "lpa_code": self.LPA_CODE,
            "activation_code": self.ACTIVATION_CODE,
        }]
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(caught.exception.code, "banana_invalid_response")
        logged = output.getvalue()
        self.assertIn("BANANA_DETAILS_RESPONSE response_type=list count=1", logged)
        self.assertNotIn(self.ICCID, logged)
        self.assertNotIn(self.LPA_CODE, logged)
        self.assertNotIn(self.ACTIVATION_CODE, logged)

    def test_diagnostics_never_log_line_secrets(self):
        response = {
            "sim_card": {
                "iccid": self.ICCID,
                "lpa_code": self.LPA_CODE,
                "activation_code": self.ACTIVATION_CODE,
                "remaining_usage_kb": "not-an-int",
            }
        }
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError):
            self.client_with_response(response).get_details(self.ICCID)
        logged = output.getvalue()
        self.assertIn(f"iccid_length={len(self.ICCID)}", logged)
        self.assertIn("sim_card_keys=activation_code,iccid,lpa_code,remaining_usage_kb", logged)
        self.assertNotIn(self.ICCID, logged)
        self.assertNotIn(self.LPA_CODE, logged)
        self.assertNotIn(self.ACTIVATION_CODE, logged)

    def test_esim_list_is_logged_structurally_without_values(self):
        secrets = (
            self.ICCID,
            self.LPA_CODE,
            self.ACTIVATION_CODE,
            "+79991234567",
            "pin-very-secret",
            "puk-very-secret",
            "https://install.example/very-secret",
            "987654321",
            "2026-12-31T23:59:59Z",
        )
        response = {
            "esimList": [{
                "iccid": self.ICCID,
                "lpa_code": self.LPA_CODE,
                "activation_code": self.ACTIVATION_CODE,
                "msisdn": secrets[3],
                "pin": secrets[4],
                "puk": secrets[5],
                "install_url": secrets[6],
                "remaining": 987654321,
                "expires_at": secrets[8],
                "usage": {"balance": 987654321, "updated_at": secrets[8]},
                "packages": [{"url": secrets[6], "token": "token-very-secret"}],
            }],
            "pager": {"page": 1, "total": 1},
        }
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(caught.exception.code, "banana_invalid_line_response")
        logged = output.getvalue()
        self.assertIn("keys=esimList,pager", logged)
        self.assertIn("esimList_present=true", logged)
        self.assertIn("esimList_type=list", logged)
        self.assertIn("esimList_count=1", logged)
        self.assertIn("esimList_first_type=dict", logged)
        self.assertIn("usage:dict(keys=balance,updated_at)", logged)
        self.assertIn("packages:list(count=1,first_type=dict,first_keys=token,url)", logged)
        for secret in (*secrets, "token-very-secret"):
            self.assertNotIn(secret, logged)

    def test_single_esim_list_entry_is_normalized(self):
        response = {
            "esimList": [{
                "iccid": self.ICCID,
                "esimStatus": "IN_USE",
                "totalVolume": 10 * 1024 + 511,
                "orderUsage": 3 * 1024 + 256,
                "expiredTime": "2030-12-31T23:59:59Z",
                "totalDuration": 30,
                "durationUnit": "DAY",
                "supportTopUpType": 2,
                "packageList": [{"packageCode": "secret-package"}],
            }],
            "pager": {"page": 1, "total": 1},
        }
        with redirect_stdout(io.StringIO()):
            result = self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(result["pager"], response["pager"])
        self.assertEqual(result["sim_card"], {
            "iccid": self.ICCID,
            "allowed_usage_kb": 10,
            "remaining_usage_kb": 7,
            "status": "IN_USE",
            "refillable": True,
            "expires_at": "2030-12-31T23:59:59Z",
        })
        self.assertNotIn("remaining_days", result["sim_card"])

    def test_esim_list_selects_exact_matching_iccid_not_first(self):
        other_iccid = "8985201234567890999"
        response = {
            "esimList": [
                {
                    "iccid": other_iccid,
                    "esimStatus": "IN_USE",
                    "totalVolume": 999 * 1024,
                    "orderUsage": 1,
                    "supportTopUpType": 3,
                },
                {
                    "iccid": self.ICCID,
                    "esimStatus": "GOT_RESOURCE",
                    "totalVolume": 2048,
                    "orderUsage": 1024,
                    "supportTopUpType": 1,
                },
            ],
            "pager": {"page": 1, "total": 2},
        }
        with redirect_stdout(io.StringIO()):
            card = self.client_with_response(response).get_details(self.ICCID)["sim_card"]
        self.assertEqual(card["iccid"], self.ICCID)
        self.assertEqual(card["allowed_usage_kb"], 2)
        self.assertEqual(card["remaining_usage_kb"], 1)
        self.assertEqual(card["status"], "GOT_RESOURCE")
        self.assertFalse(card["refillable"])

    def test_esim_list_without_matching_iccid_is_rejected(self):
        response = {
            "esimList": [{
                "iccid": "8985201234567890999",
                "totalVolume": 2048,
                "orderUsage": 0,
                "supportTopUpType": 2,
            }],
            "pager": {},
        }
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            self.client_with_response(response).get_details(self.ICCID)
        self.assertEqual(caught.exception.code, "banana_invalid_line_response")
        self.assertIn(
            "BANANA_DETAILS_INVALID reason=esim_iccid_not_found",
            output.getvalue(),
        )

    def test_esim_list_usage_values_are_strict_nonnegative_integers(self):
        cases = (
            (True, 0, "invalid_total_volume_type"),
            ("2048", 0, "invalid_total_volume_type"),
            (-1, 0, "invalid_total_volume_type"),
            (2048, True, "invalid_order_usage_type"),
            (2048, "1024", "invalid_order_usage_type"),
            (2048, -1, "invalid_order_usage_type"),
            (1024, 1025, "order_usage_exceeds_total_volume"),
        )
        for total_volume, order_usage, reason in cases:
            with self.subTest(total_volume=total_volume, order_usage=order_usage):
                response = {
                    "esimList": [{
                        "iccid": self.ICCID,
                        "totalVolume": total_volume,
                        "orderUsage": order_usage,
                        "supportTopUpType": 2,
                    }],
                    "pager": {},
                }
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(BananaError) as caught:
                    self.client_with_response(response).get_details(self.ICCID)
                self.assertEqual(caught.exception.code, "banana_invalid_line_response")
                self.assertIn(f"BANANA_DETAILS_INVALID reason={reason}", output.getvalue())

    def test_esim_list_support_topup_type_mapping_is_conservative(self):
        for support_type, expected in ((1, False), (2, True), (3, True), (0, False), (True, False), ("2", False)):
            with self.subTest(support_type=support_type):
                response = {
                    "esimList": [{
                        "iccid": self.ICCID,
                        "totalVolume": 1024,
                        "orderUsage": 1024,
                        "supportTopUpType": support_type,
                    }],
                    "pager": {},
                }
                with redirect_stdout(io.StringIO()):
                    card = self.client_with_response(response).get_details(self.ICCID)["sim_card"]
                self.assertIs(card["refillable"], expected)

    def test_refill_dict_is_logged_structurally_without_values(self):
        response = {
            "result": {
                "iccid": self.ICCID,
                "activation_code": self.ACTIVATION_CODE,
                "remaining": 987654321,
            },
            "items": [{"lpa_code": self.LPA_CODE, "token": "token-very-secret"}],
        }
        client = self.client_with_response((response, 202))
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            client.refill(47, self.ICCID, 330)
        self.assertEqual(caught.exception.code, "banana_invalid_refill_response")
        logged = output.getvalue()
        self.assertIn("BANANA_REFILL_RESPONSE http_status=202 response_type=dict", logged)
        self.assertIn("keys=items,result", logged)
        self.assertIn("result:dict(keys=activation_code,iccid,remaining)", logged)
        self.assertIn("items:list(count=1,first_type=dict,first_keys=lpa_code,token)", logged)
        self.assertIn("BANANA_REFILL_INVALID reason=missing_success", logged)
        for secret in (self.ICCID, self.LPA_CODE, self.ACTIVATION_CODE, "987654321", "token-very-secret"):
            self.assertNotIn(secret, logged)

    def test_refill_list_is_logged_structurally_without_values(self):
        response = [{
            "iccid": self.ICCID,
            "lpa_code": self.LPA_CODE,
            "payload": {"activation_code": self.ACTIVATION_CODE},
        }]
        client = self.client_with_response((response, 200))
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError) as caught:
            client.refill(47, self.ICCID, 330)
        self.assertEqual(caught.exception.code, "banana_invalid_refill_response")
        logged = output.getvalue()
        self.assertIn("BANANA_REFILL_RESPONSE http_status=200 response_type=list count=1", logged)
        self.assertIn("first_keys=iccid,lpa_code,payload", logged)
        self.assertIn("payload:dict(keys=activation_code)", logged)
        self.assertIn("BANANA_REFILL_INVALID reason=unexpected_response_shape", logged)
        for secret in (self.ICCID, self.LPA_CODE, self.ACTIVATION_CODE):
            self.assertNotIn(secret, logged)

    def test_standard_refill_success_still_passes(self):
        response = {"success": True, "iccid": self.ICCID}
        client = self.client_with_response((response, 200))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(client.refill(47, self.ICCID, 330), response)
        logged = output.getvalue()
        self.assertIn("BANANA_REFILL_RESPONSE http_status=200 response_type=dict", logged)
        self.assertNotIn(self.ICCID, logged)

    def test_wrapper_refill_success_with_matching_iccid_passes(self):
        response = {
            "success": True,
            "errorCode": 0,
            "errorMsg": "",
            "obj": {"iccid": self.ICCID, "refillId": "secret-refill-id"},
        }
        client = self.client_with_response((response, 200))
        output = io.StringIO()
        with redirect_stdout(output):
            result = client.refill(48, self.ICCID, 330)
        self.assertEqual(result, response["obj"])
        self.assertNotIn(self.ICCID, output.getvalue())
        self.assertNotIn("secret-refill-id", output.getvalue())

    def test_wrapper_refill_success_without_iccid_passes(self):
        response = {
            "success": True,
            "errorCode": 0,
            "errorMsg": "",
            "obj": {"refillId": "secret-refill-id"},
        }
        client = self.client_with_response((response, 200))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(client.refill(48, self.ICCID, 330), response["obj"])

    def test_wrapper_refill_success_with_mismatched_iccid_is_rejected(self):
        for returned_iccid in ("8985201234567890999", None, 8985201234567890123):
            with self.subTest(returned_iccid=returned_iccid):
                response = {
                    "success": True,
                    "errorCode": 0,
                    "errorMsg": "",
                    "obj": {"iccid": returned_iccid},
                }
                client = self.client_with_response((response, 200))
                with redirect_stdout(io.StringIO()), self.assertRaises(BananaError) as caught:
                    client.refill(48, self.ICCID, 330)
                self.assertEqual(caught.exception.code, "banana_line_mismatch")

    def test_wrapper_refill_success_false_is_not_accepted(self):
        response = {
            "success": False,
            "errorCode": "refill_rejected",
            "errorMsg": "rejected",
            "obj": {"iccid": self.ICCID},
        }
        client = self.client_with_response((response, 200))
        with redirect_stdout(io.StringIO()), self.assertRaises(BananaError) as caught:
            client.refill(48, self.ICCID, 330)
        self.assertEqual(caught.exception.code, "refill_rejected")

    def test_refill_invalid_reasons_distinguish_type_and_false(self):
        for value, reason in ((1, "success_wrong_type"), (False, "success_false")):
            with self.subTest(value=value):
                client = self.client_with_response(({"success": value}, 200))
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(BananaError) as caught:
                    client.refill(47, self.ICCID, 330)
                self.assertEqual(caught.exception.code, "banana_invalid_refill_response")
                self.assertIn(f"BANANA_REFILL_INVALID reason={reason}", output.getvalue())

    def test_internal_reasons_cover_required_validation_failures(self):
        reason = BananaClient._details_invalid_reason
        self.assertEqual(reason({"sim_card": []}, self.ICCID), "invalid_sim_card_type")
        self.assertEqual(reason({"sim_card": {}}, self.ICCID), "missing_iccid")
        self.assertEqual(
            reason({"sim_card": {"iccid": "8985201234567890999"}}, self.ICCID),
            "iccid_mismatch",
        )
        self.assertEqual(
            reason({"sim_card": {"iccid": self.ICCID, "allowed_usage_kb": "1"}}, self.ICCID),
            "invalid_allowed_usage_type",
        )
        self.assertEqual(
            reason({"sim_card": {"iccid": self.ICCID, "remaining_days": "30"}}, self.ICCID),
            "invalid_remaining_days_type",
        )


if __name__ == "__main__":
    unittest.main()
