"""
v2.lib.admin_command_handler — deterministic admin command core.

This module is intentionally pure: no LINE API calls, no customer replies,
no env reads, and no network. A future LINE webhook adapter can pass staff
messages into parse_admin_command()/handle_admin_command() and send the
returned admin_text to the staff LINE group.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from . import redactor
from .admin_ops import (
    AdminCaseSummary,
    OpenHandoffBrief,
    get_admin_case,
    list_admin_cases,
    list_open_handoffs,
    pause_bot_for_customer,
    resume_bot_for_customer,
)
from .response_writer import _WHOLESALE_BLACKLIST


_WHOLESALE_REDACTION_TOKEN = "***WHOLESALE-REDACTED***"
_DEFAULT_LIMIT = 5


@dataclass(frozen=True)
class AdminCommand:
    action: str
    target: Optional[str] = None
    reason: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdminCommandResult:
    ok: bool
    action: str
    admin_text: str
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    mutated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scrub_wholesale(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = str(text)
    for pat in _WHOLESALE_BLACKLIST:
        out = pat.sub(_WHOLESALE_REDACTION_TOKEN, out)
    return out


def _safe_text(text: str) -> str:
    out = redactor.redact(_scrub_wholesale(text) or "")
    # Staff LINE output should not keep recognizable secret prefixes.
    return out.replace(f"s{'k'}-***REDACTED***", "***REDACTED***")


def _split_words(text: str) -> list[str]:
    return [p for p in (text or "").strip().split() if p]


def parse_admin_command(text: str) -> AdminCommand:
    """
    Parse staff LINE text into a deterministic command.

    Supported commands:
    - cases
    - cases paused
    - handoffs
    - case <psid_or_conversation_id>
    - pause <psid_or_conversation_id> [reason...]
    - resume <psid_or_conversation_id> [reason...]
    - help
    """
    raw = text or ""
    parts = _split_words(raw)
    if not parts:
        return AdminCommand(action="help", raw_text=raw)

    head = parts[0].lower()
    second = parts[1].lower() if len(parts) > 1 else ""

    if head in {"help", "?", "commands"}:
        return AdminCommand(action="help", raw_text=raw)
    if head in {"cases", "case-list"}:
        if second in {"paused", "pause", "human_paused"}:
            return AdminCommand(action="cases_paused", raw_text=raw)
        return AdminCommand(action="cases", raw_text=raw)
    if head in {"handoffs", "handoff"}:
        return AdminCommand(action="handoffs", raw_text=raw)
    if head == "case" and len(parts) >= 2:
        return AdminCommand(action="case", target=parts[1], raw_text=raw)
    if head == "pause" and len(parts) >= 2:
        return AdminCommand(
            action="pause",
            target=parts[1],
            reason=" ".join(parts[2:]).strip() or None,
            raw_text=raw,
        )
    if head == "resume" and len(parts) >= 2:
        return AdminCommand(
            action="resume",
            target=parts[1],
            reason=" ".join(parts[2:]).strip() or None,
            raw_text=raw,
        )

    return AdminCommand(action="unknown", raw_text=raw)


def _help_text() -> str:
    return (
        "คำสั่งแอดมินที่ใช้ได้:\n"
        "- cases — ดูเคสล่าสุด\n"
        "- cases paused — ดูเคสที่บอทหยุดไว้\n"
        "- handoffs — ดูเคสที่รอทีมงาน\n"
        "- case <id> — ดูรายละเอียดเคส\n"
        "- pause <id> [เหตุผล] — หยุดบอทเคสนั้น\n"
        "- resume <id> [เหตุผล] — ให้บอทกลับมาทำงาน"
    )


def _tour_line(case: AdminCaseSummary) -> str:
    if case.selected_tour:
        t = case.selected_tour
        name = _safe_text(t.name or "ไม่ระบุชื่อทัวร์")
        code = t.tour_code_real or t.web_code or t.tour_id
        return f"ทัวร์ที่เลือก: {name} ({_safe_text(str(code))})"
    if case.latest_offer:
        offer = case.latest_offer
        name = _safe_text(offer.top_tour_name or "ไม่ระบุชื่อทัวร์")
        return f"ข้อเสนอล่าสุด: {name} ({offer.tour_count} ตัวเลือก)"
    return "ทัวร์: ยังไม่มีข้อมูลที่เลือก"


def _format_case_line(case: AdminCaseSummary, index: int) -> str:
    name = _safe_text(case.display_name or case.psid_masked)
    state = _safe_text(case.conversation_state or "unknown")
    paused = "หยุดอยู่" if case.is_paused else "บอททำงาน"
    dest_bits = [b for b in (case.latest_country, case.latest_city) if b]
    dest = f" | {'/'.join(_safe_text(str(b)) for b in dest_bits)}" if dest_bits else ""
    return f"{index}. {name} | {case.psid_masked} | {state} | {paused}{dest}"


def _format_case_detail(case: AdminCaseSummary) -> str:
    name = _safe_text(case.display_name or case.psid_masked)
    state = _safe_text(case.conversation_state or "unknown")
    paused = "หยุดอยู่" if case.is_paused else "บอททำงาน"
    lines = [
        f"เคส: {name}",
        f"ID: {case.psid_masked}",
        f"สถานะ: {state} / {paused}",
    ]
    if case.latest_country or case.latest_city:
        lines.append(
            "ปลายทาง: "
            + _safe_text(" / ".join(str(x) for x in (case.latest_country, case.latest_city) if x))
        )
    if case.budget_per_person:
        lines.append(f"งบ: {case.budget_per_person:,} บาท/คน")
    if case.pax_count:
        lines.append(f"จำนวน: {case.pax_count} คน")
    if case.travel_month:
        lines.append(f"ช่วงเดินทาง: {_safe_text(str(case.travel_month))}")
    lines.append(_tour_line(case))
    if case.open_handoff:
        h = case.open_handoff
        lines.append(f"Handoff: {h.trigger_type} ({h.id})")
        if h.trigger_detail_summary:
            lines.append(f"เหตุผล: {_safe_text(h.trigger_detail_summary)}")
    if case.paused_reason:
        lines.append(f"เหตุผลที่หยุด: {_safe_text(case.paused_reason)}")
    return "\n".join(lines)


def _format_handoff_line(handoff: OpenHandoffBrief, index: int) -> str:
    detail = f" | {_safe_text(handoff.trigger_detail_summary)}" if handoff.trigger_detail_summary else ""
    return (
        f"{index}. {handoff.psid_masked} | {handoff.trigger_type} | "
        f"{_safe_text(handoff.triggered_at or '-')}{detail}"
    )


def _resolve_case(supabase, target: str, memory=None) -> Optional[AdminCaseSummary]:
    case = get_admin_case(supabase, psid=target, memory=memory)
    if case:
        return case
    return get_admin_case(supabase, conversation_id=target, memory=memory)


def handle_admin_command(
    command_or_text: AdminCommand | str,
    supabase,
    *,
    admin_user_id: str,
    memory=None,
    now=None,
) -> AdminCommandResult:
    """Execute an admin command and return staff-safe text."""
    del now  # reserved for future time-dependent formatting
    command = (
        parse_admin_command(command_or_text)
        if isinstance(command_or_text, str)
        else command_or_text
    )

    if command.action in {"help", "unknown"}:
        prefix = "ไม่เข้าใจคำสั่งนี้\n\n" if command.action == "unknown" else ""
        return AdminCommandResult(
            ok=command.action == "help",
            action=command.action,
            admin_text=prefix + _help_text(),
            error=None if command.action == "help" else "unknown_command",
        )

    if command.action in {"cases", "cases_paused"}:
        cases = list_admin_cases(
            supabase,
            memory=memory,
            limit=_DEFAULT_LIMIT,
            only_open=True,
            only_paused=(command.action == "cases_paused"),
        )
        title = "เคสที่หยุดบอทไว้" if command.action == "cases_paused" else "เคสล่าสุด"
        if not cases:
            return AdminCommandResult(
                ok=True,
                action=command.action,
                admin_text=f"{title}: ยังไม่มีรายการ",
                data={"cases": []},
            )
        lines = [title]
        lines.extend(_format_case_line(c, i + 1) for i, c in enumerate(cases))
        return AdminCommandResult(
            ok=True,
            action=command.action,
            admin_text="\n".join(lines),
            data={"cases": [c.to_dict() for c in cases]},
        )

    if command.action == "handoffs":
        handoffs = list_open_handoffs(supabase, limit=_DEFAULT_LIMIT)
        if not handoffs:
            return AdminCommandResult(
                ok=True,
                action="handoffs",
                admin_text="Handoffs: ยังไม่มีรายการที่รอทีมงาน",
                data={"handoffs": []},
            )
        lines = ["Handoffs ที่รอทีมงาน"]
        lines.extend(_format_handoff_line(h, i + 1) for i, h in enumerate(handoffs))
        return AdminCommandResult(
            ok=True,
            action="handoffs",
            admin_text="\n".join(lines),
            data={"handoffs": [h.to_dict() for h in handoffs]},
        )

    if command.action == "case":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False,
                action="case",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}",
                error="case_not_found",
            )
        return AdminCommandResult(
            ok=True,
            action="case",
            admin_text=_format_case_detail(case),
            data={"case": case.to_dict()},
        )

    if command.action == "pause":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False,
                action="pause",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}\nยังไม่ได้หยุดบอท",
                error="case_not_found",
            )
        result = pause_bot_for_customer(
            supabase,
            psid=case.psid,
            paused_by="admin",
            reason=command.reason or f"admin command by {admin_user_id}",
        )
        case_name = _safe_text(case.display_name or case.psid_masked)
        return AdminCommandResult(
            ok=True,
            action="pause",
            admin_text=(
                f"หยุดบอทให้เคส {case_name} แล้ว\n"
                f"ID: {result.psid_masked}\n"
                f"ถึง: {_safe_text(result.pause_until)}"
            ),
            data={"pause": result.to_dict()},
            mutated=True,
        )

    if command.action == "resume":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False,
                action="resume",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}\nยังไม่ได้ resume บอท",
                error="case_not_found",
            )
        result = resume_bot_for_customer(
            supabase,
            psid=case.psid,
            resumed_by=admin_user_id,
            reason=command.reason,
        )
        case_name = _safe_text(case.display_name or case.psid_masked)
        return AdminCommandResult(
            ok=True,
            action="resume",
            admin_text=(
                f"เปิดบอทกลับให้เคส {case_name} แล้ว\n"
                f"ID: {result.psid_masked}\n"
                f"handoff ปิดแล้ว: {result.handoffs_closed}"
            ),
            data={"resume": result.to_dict()},
            mutated=True,
        )

    return AdminCommandResult(
        ok=False,
        action=command.action,
        admin_text="ไม่เข้าใจคำสั่งนี้\n\n" + _help_text(),
        error="unsupported_command",
    )


__all__ = [
    "AdminCommand",
    "AdminCommandResult",
    "parse_admin_command",
    "handle_admin_command",
]
