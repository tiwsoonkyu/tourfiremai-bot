"""Sprint 2 test: redactor masks secrets and PII."""

import pytest
from v2.lib.redactor import redact, redact_event, _mask_psid


class TestRedactStrings:
    def test_anthropic_key(self):
        s = "key=sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx hello"
        out = redact(s)
        assert "sk-ant-api03" not in out
        assert "sk-ant-***REDACTED***" in out

    def test_openai_key(self):
        s = "OPENAI_API_KEY=sk-proj-AbCdEfGhIjKlMnOpQrStUvWx"
        assert "sk-proj-" not in redact(s)

    def test_fb_token(self):
        s = "token=EAAR7Ct4VPOMBRYpA3TKYwKxs9QC9hHjrc9heExIvQTd"
        assert "EAAR7Ct4VPOM" not in redact(s)
        assert "EAA-***REDACTED***" in redact(s)

    def test_email(self):
        assert "user@example.com" not in redact("send to user@example.com please")

    def test_thai_phone(self):
        assert "0812345678" not in redact("โทร 0812345678 ค่ะ")
        assert "+66812345678" not in redact("call +66812345678")

    def test_telegram_token(self):
        assert "8550125467:AAEBlRvjxcWhNj_CJ-ySswB2VAUcdmEL" not in redact(
            "bot8550125467:AAEBlRvjxcWhNj_CJ-ySswB2VAUcdmELek0"
        )

    def test_non_string_returns_as_is(self):
        assert redact(None) is None
        assert redact(123) == 123


class TestPsidMask:
    def test_typical_psid(self):
        # PSID format: 15-17 digits
        masked = _mask_psid("1234567890123456")
        assert masked.startswith("1234")
        assert masked.endswith("56")
        assert "*" in masked

    def test_short_id_default(self):
        assert _mask_psid("123") == "***PSID***"


class TestRedactEvent:
    def test_sensitive_keys_masked(self):
        event = {
            "password": "secret",
            "api_key": "sk-xxx",
            "x-api-key": "sk-yyy",
            "ok_field": "fine",
        }
        out = redact_event(event)
        assert out["password"] == "***REDACTED***"
        assert out["api_key"] == "***REDACTED***"
        assert out["x-api-key"] == "***REDACTED***"
        assert out["ok_field"] == "fine"

    def test_psid_masked_in_dict(self):
        event = {"sender": {"id": "1234567890123456"}}
        out = redact_event(event)
        # 'id' value matching PSID pattern is masked via _PSID_RE during string traversal
        # But 'sender_id' / 'psid' keys are masked explicitly
        assert "1234567890123456" not in str(out)

    def test_psid_key_masked(self):
        event = {"psid": "1234567890123456"}
        out = redact_event(event)
        assert out["psid"] != "1234567890123456"

    def test_nested_event(self):
        event = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "1234567890123456"},
                    "message": {"text": "send to user@x.com"}
                }]
            }]
        }
        out = redact_event(event)
        flat = str(out)
        assert "user@x.com" not in flat
        assert "1234567890123456" not in flat

    def test_depth_limit(self):
        # Don't crash on deep nesting
        deep = {"a": {}}
        cur = deep["a"]
        for _ in range(25):
            cur["nest"] = {}
            cur = cur["nest"]
        cur["api_key"] = "sk-xxx"
        out = redact_event(deep)
        assert out is not None
