"""
v2.lib.meta_sender - tiny Meta Messenger send adapter.

This module is intentionally small and stdlib-only. It never raises to the
webhook caller and never logs or returns the page access token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MetaSendResult:
    ok: bool
    status_code: Optional[int] = None
    response_text: str = ""
    error: Optional[str] = None


class MetaMessengerSender:
    """Send plain text replies through Meta Messenger Send API."""

    def __init__(
        self,
        page_access_token: Optional[str],
        *,
        api_version: str = "v21.0",
        urlopen: Optional[Any] = None,
        timeout: int = 10,
    ):
        self.page_access_token = page_access_token
        self.api_version = api_version
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = int(timeout)

    def send_text(self, psid: str, text: str) -> MetaSendResult:
        if not self.page_access_token:
            return MetaSendResult(ok=False, error="missing_page_access_token")
        if not psid:
            return MetaSendResult(ok=False, error="missing_psid")
        if not text:
            return MetaSendResult(ok=False, error="empty_text")

        url = (
            f"https://graph.facebook.com/{self.api_version}/me/messages"
            f"?access_token={self.page_access_token}"
        )
        payload = {
            "recipient": {"id": str(psid)},
            "message": {"text": str(text)[:1900]},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return MetaSendResult(
                    ok=200 <= int(resp.status) < 300,
                    status_code=int(resp.status),
                    response_text=raw[:1000],
                )
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            return MetaSendResult(
                ok=False,
                status_code=e.code,
                response_text=raw[:1000],
                error=f"http_{e.code}",
            )
        except Exception as e:  # pragma: no cover - defensive around network I/O
            return MetaSendResult(ok=False, error=type(e).__name__)
