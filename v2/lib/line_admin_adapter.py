"""
v2.lib.line_admin_adapter — DEV-2026-05-19-008.

Deterministic LINE admin command adapter. Sits BETWEEN a future LINE OA
webhook and the existing :mod:`v2.lib.admin_command_handler` core. The
adapter enforces a sender allow-list before any admin command is parsed,
so an unauthorized LINE user/group cannot:

    - pause/resume a customer
    - mark a tour or page post `full` / `sold_out`
    - clear an availability override
    - read admin case lists or page-post details

Public API:

    AdminAllowList            — frozen dataclass wrapping allowed sender ids
    AdminAllowList.from_env() — read V2_STAGING_LINE_ADMIN_ALLOW_LIST
                                (and LINE_ADMIN_USER_OR_GROUP_ID fallback)
    LineAdminAdapter          — wraps allow-list + supabase + memory
    LineAdminAdapter.handle(sender_id, text, ...) -> AdminCommandResult

Hard rules (enforced by `v2/tests/test_line_admin_adapter.py`):

    - No live LINE Messaging API calls. No env reads in tests — caller may
      pass `allow_list` explicitly.
    - Unauthorized senders never reach `parse_admin_command()` /
      `handle_admin_command()` — the adapter short-circuits with a denial.
    - Unauthorized commands have NO side effects (no row insert/update).
    - Unauthorized denials never echo the raw command text back (avoids
      reflection of attacker-controlled content) and never leak the
      allow-list contents.
    - PSIDs / secrets / wholesale brand names are scrubbed via the same
      utilities the admin command handler uses (`redactor` +
      `_WHOLESALE_BLACKLIST`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from . import redactor
from .admin_command_handler import (
    AdminCommandResult,
    handle_admin_command,
    parse_admin_command,
)

logger = logging.getLogger("v2.line_admin_adapter")

# Env var lookups. Only the V2 staging-prefixed name is authoritative;
# `LINE_ADMIN_USER_OR_GROUP_ID` (without prefix) is read via the same
# mechanism as `v2.lib.config` so a single staging admin id from the
# Config dataclass still works.
_ENV_ALLOW_LIST = "V2_STAGING_LINE_ADMIN_ALLOW_LIST"
_ENV_FALLBACK_SINGLE = "V2_STAGING_LINE_ADMIN_USER_OR_GROUP_ID"

# LINE user/group ids are stable opaque strings (U... / C... / R...). Cap
# length to refuse arbitrary user-controlled junk.
_MAX_SENDER_LEN = 200


@dataclass(frozen=True)
class AdminAllowList:
    """
    Allow-listed LINE sender ids — user ids OR group ids. Membership is
    case-sensitive; LINE ids are case-sensitive opaque strings, so we keep
    them as-is.

    `ids` is a frozenset for O(1) membership tests and to discourage
    accidental mutation by callers.
    """
    ids: frozenset = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Normalise: strip whitespace, drop empties, drop oversize.
        cleaned = {
            _normalise_sender(i) for i in self.ids if _is_safe_id(i)
        }
        cleaned.discard(None)
        # Cast through object.__setattr__ since frozen=True.
        object.__setattr__(self, "ids", frozenset(cleaned))

    def is_allowed(self, sender_id) -> bool:
        s = _normalise_sender(sender_id)
        if s is None:
            return False
        return s in self.ids

    def is_empty(self) -> bool:
        return not self.ids

    def to_dict(self) -> dict:
        # NEVER include the raw list in any user-facing payload — for
        # debugging only. We only return the count.
        return {"allowed_count": len(self.ids)}

    @classmethod
    def from_iterable(cls, ids: Iterable) -> "AdminAllowList":
        return cls(ids=frozenset(ids or ()))

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "AdminAllowList":
        """
        Read allow-list from environment. Comma- or whitespace-separated
        values are accepted. Falls back to the single-admin env var when
        the multi-id var is not set.
        """
        if env is None:
            env = os.environ
        raw = env.get(_ENV_ALLOW_LIST)
        if not raw:
            raw = env.get(_ENV_FALLBACK_SINGLE)
        if not raw:
            return cls(ids=frozenset())
        parts = [
            p.strip() for chunk in raw.replace(";", ",").split(",")
            for p in chunk.split()
        ]
        ids = frozenset(p for p in parts if p)
        return cls(ids=ids)


def _is_safe_id(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if not s:
        return False
    if len(s) > _MAX_SENDER_LEN:
        return False
    if any(c in s for c in ("\n", "\r", "\t", " ")):
        # LINE ids have no whitespace; if any is found this is malformed.
        return False
    return True


def _normalise_sender(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or len(s) > _MAX_SENDER_LEN:
        return None
    if any(c in s for c in ("\n", "\r", "\t", " ")):
        return None
    return s


def _mask_sender(sender_id) -> str:
    """
    Mask a LINE sender id for safe logging. Keeps first 2 chars + last 4.
    """
    s = _normalise_sender(sender_id)
    if not s:
        return "***"
    if len(s) <= 8:
        return f"{s[:2]}***"
    return f"{s[:2]}***{s[-4:]}"


@dataclass(frozen=True)
class _Denial:
    sender_masked: str
    reason: str  # "not_allowed" | "empty_allow_list" | "missing_sender"


class LineAdminAdapter:
    """
    Allow-list-gated admin command adapter.

    Construction:

        adapter = LineAdminAdapter(
            supabase=supabase,
            allow_list=AdminAllowList.from_iterable(["Uadmin1", "Cgroup1"]),
            memory=memory_service,           # optional
        )

    Usage:

        result = adapter.handle(sender_id="Uadmin1", text="cases")
        # result: AdminCommandResult — `ok=False, error='not_allowed'` for
        # denied senders, otherwise whatever the admin handler returned.
    """

    def __init__(self, *, supabase, allow_list: AdminAllowList,
                 memory=None) -> None:
        if supabase is None:
            raise ValueError("LineAdminAdapter: supabase is required")
        if not isinstance(allow_list, AdminAllowList):
            raise TypeError(
                "LineAdminAdapter: allow_list must be an AdminAllowList"
            )
        self._supabase = supabase
        self._allow_list = allow_list
        self._memory = memory

    @property
    def allow_list(self) -> AdminAllowList:
        return self._allow_list

    def is_authorized(self, sender_id) -> bool:
        if self._allow_list.is_empty():
            return False
        return self._allow_list.is_allowed(sender_id)

    def _denial(self, sender_id, reason: str) -> AdminCommandResult:
        masked = _mask_sender(sender_id)
        logger.warning(
            "line_admin_adapter: denied sender=%s reason=%s allow_count=%d",
            masked, reason, len(self._allow_list.ids),
        )
        return AdminCommandResult(
            ok=False,
            action="denied",
            admin_text=(
                "คำสั่งนี้ใช้ได้เฉพาะแอดมินที่ได้รับอนุญาตเท่านั้น "
                "หากต้องการสิทธิ์โปรดติดต่อทีมงาน"
            ),
            error=reason,
            mutated=False,
        )

    def handle(self, *, sender_id, text: str,
               memory=None) -> AdminCommandResult:
        """
        Parse + dispatch an admin command. Returns an AdminCommandResult.

        Unauthorized sender → AdminCommandResult(ok=False, action='denied',
        error='not_allowed'|'empty_allow_list'|'missing_sender'). No side
        effects.
        """
        # 1. Sender must be present and well-formed.
        if not _normalise_sender(sender_id):
            return self._denial(sender_id, "missing_sender")

        # 2. Allow-list must be non-empty (defence against
        #    misconfiguration that would otherwise allow nobody).
        if self._allow_list.is_empty():
            return self._denial(sender_id, "empty_allow_list")

        # 3. Allow-list must contain this sender.
        if not self._allow_list.is_allowed(sender_id):
            return self._denial(sender_id, "not_allowed")

        # 4. Authorized — parse + dispatch via existing core. Note: we
        # pass the normalised sender id as `admin_user_id` so audit logs
        # in `admin_ops.pause_bot_for_customer` etc. record the actual
        # caller and not the raw env-injected fallback.
        admin_user_id = _normalise_sender(sender_id)
        command = parse_admin_command(text)
        try:
            return handle_admin_command(
                command, self._supabase,
                admin_user_id=admin_user_id,
                memory=memory if memory is not None else self._memory,
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.exception(
                "line_admin_adapter: handler crashed for sender=%s: %s",
                _mask_sender(sender_id), redactor.redact(str(e)),
            )
            return AdminCommandResult(
                ok=False, action="error",
                admin_text="เกิดข้อผิดพลาดในการประมวลคำสั่ง",
                error="handler_error",
                mutated=False,
            )


__all__ = [
    "AdminAllowList",
    "LineAdminAdapter",
]
