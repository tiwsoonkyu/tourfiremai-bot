"""Unit tests for the Meta Messenger send adapter."""

import json

from v2.lib.meta_sender import MetaMessengerSender


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"recipient_id":"PSID1","message_id":"m_mid"}'


def test_missing_page_token_fails_closed_without_network():
    called = {"n": 0}

    def _urlopen(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("network must not be called")

    result = MetaMessengerSender(None, urlopen=_urlopen).send_text("PSID1", "hello")

    assert result.ok is False
    assert result.error == "missing_page_access_token"
    assert called["n"] == 0


def test_send_text_posts_expected_graph_payload():
    captured = {}

    def _urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["content_type"] = req.headers.get("Content-type")
        return _FakeResponse()

    result = MetaMessengerSender("PAGE_TOKEN", urlopen=_urlopen, timeout=7).send_text(
        "PSID1", "สวัสดีค่ะ"
    )

    assert result.ok is True
    assert result.status_code == 200
    assert "access_token=PAGE_TOKEN" in captured["url"]
    assert captured["timeout"] == 7
    assert captured["body"] == {
        "recipient": {"id": "PSID1"},
        "message": {"text": "สวัสดีค่ะ"},
    }
