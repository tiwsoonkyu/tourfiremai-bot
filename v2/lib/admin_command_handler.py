"""
v2.lib.admin_command_handler — deterministic admin command core.

This module is intentionally pure: no LINE API calls, no customer replies,
no env reads, and no network. A future LINE webhook adapter can pass staff
messages into parse_admin_command()/handle_admin_command() and send the
returned admin_text to the staff LINE group.
"""

from __future__ import annotations

import re
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
from .page_post_context import (
    PagePostSummary,
    clear_availability_override,
    get_source_context,
    list_recent_page_posts,
    mark_availability_override,
)
from .response_writer import _WHOLESALE_BLACKLIST
from .tour_codes import classify_code


_WHOLESALE_REDACTION_TOKEN = "***WHOLESALE-REDACTED***"
_DEFAULT_LIMIT = 5
_POSTS_LIMIT = 5
_POST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,}$")


@dataclass(frozen=True)
class AdminCommand:
    action: str
    target: Optional[str] = None
    reason: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AdminCommandResult:
    ok: bool
    action: str
    admin_text: str
    data: Optional[dict] = None
    error: Optional[str] = None
    mutated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _scrub_wholesale(text):
    if not text:
        return text
    out = str(text)
    for pat in _WHOLESALE_BLACKLIST:
        out = pat.sub(_WHOLESALE_REDACTION_TOKEN, out)
    return out


def _safe_text(text):
    out = redactor.redact(_scrub_wholesale(text) or "")
    return out.replace(f"s{'k'}-***REDACTED***", "***REDACTED***")


def _split_words(text):
    return [p for p in (text or "").strip().split() if p]


def parse_admin_command(text):
    """
    Parse staff LINE text into a deterministic command.
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
    if head in {"posts", "post-list"}:
        return AdminCommand(action="posts", raw_text=raw)
    if head == "post" and len(parts) >= 2:
        return AdminCommand(action="post", target=parts[1], raw_text=raw)
    if head == "case" and len(parts) >= 2:
        return AdminCommand(action="case", target=parts[1], raw_text=raw)
    if head == "pause" and len(parts) >= 2:
        return AdminCommand(
            action="pause", target=parts[1],
            reason=" ".join(parts[2:]).strip() or None, raw_text=raw,
        )
    if head == "resume" and len(parts) >= 2:
        return AdminCommand(
            action="resume", target=parts[1],
            reason=" ".join(parts[2:]).strip() or None, raw_text=raw,
        )
    if head in {"mark_full", "mark-full", "markfull"} and len(parts) >= 2:
        return AdminCommand(
            action="mark_full", target=parts[1],
            reason=" ".join(parts[2:]).strip() or None, raw_text=raw,
        )
    if head in {"mark_sold_out", "mark-sold-out", "marksoldout"} and len(parts) >= 2:
        return AdminCommand(
            action="mark_sold_out", target=parts[1],
            reason=" ".join(parts[2:]).strip() or None, raw_text=raw,
        )
    if head in {"clear_full", "clear-full", "clearfull"} and len(parts) >= 2:
        return AdminCommand(action="clear_full", target=parts[1], raw_text=raw)
    if head in {"clear_sold_out", "clear-sold-out", "clearsoldout"} and len(parts) >= 2:
        return AdminCommand(action="clear_sold_out", target=parts[1], raw_text=raw)

    return AdminCommand(action="unknown", raw_text=raw)


def _help_text():
    return (
        "คำสั่งแอดมินที่ใช้ได้:\n"
        "- cases — ดูเคสล่าสุด\n"
        "- cases paused — ดูเคสที่บอทหยุดไว้\n"
        "- handoffs — ดูเคสที่รอทีมงาน\n"
        "- case <id> — ดูรายละเอียดเคส\n"
        "- pause <id> [เหตุผล] — หยุดบอทเคสนั้น\n"
        "- resume <id> [เหตุผล] — ให้บอทกลับมาทำงาน\n"
        "- posts — ดูโพสต์ล่าสุดในช่วง 3 วัน\n"
        "- post <post_id> — ดูโพสต์รายการเดียว\n"
        "- mark_full <web_code|tour_code|post_id> [เหตุผล] — แจ้งว่าทัวร์/โพสต์เต็ม\n"
        "- mark_sold_out <web_code|tour_code|post_id> [เหตุผล] — แจ้งว่าทัวร์/โพสต์ปิดรับแล้ว\n"
        "- clear_full <web_code|tour_code|post_id> — ยกเลิกสถานะ full\n"
        "- clear_sold_out <web_code|tour_code|post_id> — ยกเลิกสถานะ sold_out"
    )


# ---------------------------------------------------------------------------
# Page-post / sold-out target parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedTarget:
    kind: str
    value: Optional[str] = None
    post_row: Optional[dict] = None
    note: Optional[str] = None


def _resolve_page_post_target(supabase, target):
    if not target:
        return _ResolvedTarget(kind="unknown", note="missing target")
    raw = target.strip()
    classified = classify_code(raw)
    kind = classified.get("kind")
    value = classified.get("value")

    if kind == "web":
        return _ResolvedTarget(kind="web_code", value=value)
    if kind == "tour_code_real":
        return _ResolvedTarget(kind="tour_code_real", value=value)
    if kind == "airline":
        return _ResolvedTarget(
            kind="ambiguous",
            note=(
                "ตัวเลขสายการบินอย่างเดียวไม่พอ ขอ web_code (เช่น ap242455) "
                "หรือ tour code จริง หรือ post_id ค่ะ"
            ),
        )

    if not _POST_ID_RE.match(raw):
        return _ResolvedTarget(
            kind="unknown",
            note="รูปแบบรหัสไม่ถูก ขอ web_code / tour code จริง / post_id ค่ะ",
        )

    row = supabase.table("page_posts").select_one({"post_id": raw})
    if row is None:
        row = supabase.table("page_posts").select_one(
            {"platform": "facebook", "post_id": raw}
        )
    if row:
        return _ResolvedTarget(kind="post_id", value=raw, post_row=row)
    return _ResolvedTarget(
        kind="unknown",
        note=(
            "ไม่พบรายการที่ตรง ขอระบุชัดเจนเป็น web_code / tour code จริง / "
            "post_id ที่อยู่ในระบบค่ะ"
        ),
    )


def _format_post_summary_line(summary, index):
    title = _safe_text(summary.title) or "(ไม่มีหัวข้อ)"
    codes = " ".join(_safe_text(c) for c in summary.linked_web_codes[:3])
    bits = [f"{index}. {title}", f"id={summary.post_id}"]
    if codes:
        bits.append(f"codes={codes}")
    if summary.is_post_blocked and summary.block_status:
        bits.append(f"สถานะ={summary.block_status}")
    return " | ".join(bits)


def _format_post_detail(summary):
    title = _safe_text(summary.title) or "(ไม่มีหัวข้อ)"
    lines = [f"โพสต์: {title}", f"post_id: {summary.post_id}"]
    if summary.linked_web_codes:
        lines.append("web_codes: " + ", ".join(
            _safe_text(c) for c in summary.linked_web_codes
        ))
    if summary.linked_tour_codes_real:
        lines.append("tour codes: " + ", ".join(
            _safe_text(c) for c in summary.linked_tour_codes_real
        ))
    if summary.is_post_blocked and summary.block_status:
        lines.append(f"สถานะ: {summary.block_status}")
    if summary.posted_at:
        lines.append(f"โพสต์เมื่อ: {summary.posted_at}")
    if summary.permalink_url:
        lines.append(f"ลิงก์: {_safe_text(summary.permalink_url)}")
    return "\n".join(lines)


def _mark_status_text(status):
    return "เต็ม (full)" if status == "full" else "ปิดรับ (sold_out)"


def _tour_line(case):
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


def _format_case_line(case, index):
    name = _safe_text(case.display_name or case.psid_masked)
    state = _safe_text(case.conversation_state or "unknown")
    paused = "หยุดอยู่" if case.is_paused else "บอททำงาน"
    dest_bits = [b for b in (case.latest_country, case.latest_city) if b]
    dest = f" | {'/'.join(_safe_text(str(b)) for b in dest_bits)}" if dest_bits else ""
    return f"{index}. {name} | {case.psid_masked} | {state} | {paused}{dest}"


def _format_case_detail(case):
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


def _format_handoff_line(handoff, index):
    detail = f" | {_safe_text(handoff.trigger_detail_summary)}" if handoff.trigger_detail_summary else ""
    return (
        f"{index}. {handoff.psid_masked} | {handoff.trigger_type} | "
        f"{_safe_text(handoff.triggered_at or '-')}{detail}"
    )


def _resolve_case(supabase, target, memory=None):
    case = get_admin_case(supabase, psid=target, memory=memory)
    if case:
        return case
    return get_admin_case(supabase, conversation_id=target, memory=memory)


def handle_admin_command(command_or_text, supabase, *, admin_user_id, memory=None, now=None):
    del now
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
            supabase, memory=memory, limit=_DEFAULT_LIMIT,
            only_open=True, only_paused=(command.action == "cases_paused"),
        )
        title = "เคสที่หยุดบอทไว้" if command.action == "cases_paused" else "เคสล่าสุด"
        if not cases:
            return AdminCommandResult(
                ok=True, action=command.action,
                admin_text=f"{title}: ยังไม่มีรายการ",
                data={"cases": []},
            )
        lines = [title]
        lines.extend(_format_case_line(c, i + 1) for i, c in enumerate(cases))
        return AdminCommandResult(
            ok=True, action=command.action,
            admin_text="\n".join(lines),
            data={"cases": [c.to_dict() for c in cases]},
        )

    if command.action == "handoffs":
        handoffs = list_open_handoffs(supabase, limit=_DEFAULT_LIMIT)
        if not handoffs:
            return AdminCommandResult(
                ok=True, action="handoffs",
                admin_text="Handoffs: ยังไม่มีรายการที่รอทีมงาน",
                data={"handoffs": []},
            )
        lines = ["Handoffs ที่รอทีมงาน"]
        lines.extend(_format_handoff_line(h, i + 1) for i, h in enumerate(handoffs))
        return AdminCommandResult(
            ok=True, action="handoffs",
            admin_text="\n".join(lines),
            data={"handoffs": [h.to_dict() for h in handoffs]},
        )

    if command.action == "case":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False, action="case",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}",
                error="case_not_found",
            )
        return AdminCommandResult(
            ok=True, action="case",
            admin_text=_format_case_detail(case),
            data={"case": case.to_dict()},
        )

    if command.action == "pause":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False, action="pause",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}\nยังไม่ได้หยุดบอท",
                error="case_not_found",
            )
        result = pause_bot_for_customer(
            supabase, psid=case.psid, paused_by="admin",
            reason=command.reason or f"admin command by {admin_user_id}",
        )
        case_name = _safe_text(case.display_name or case.psid_masked)
        return AdminCommandResult(
            ok=True, action="pause",
            admin_text=(
                f"หยุดบอทให้เคส {case_name} แล้ว\n"
                f"ID: {result.psid_masked}\n"
                f"ถึง: {_safe_text(result.pause_until)}"
            ),
            data={"pause": result.to_dict()}, mutated=True,
        )

    if command.action == "posts":
        summaries = list_recent_page_posts(supabase, limit=_POSTS_LIMIT)
        if not summaries:
            return AdminCommandResult(
                ok=True, action="posts",
                admin_text="โพสต์ล่าสุด: ยังไม่มีรายการในช่วง 3 วัน",
                data={"posts": []},
            )
        lines = ["โพสต์ล่าสุด (3 วัน)"]
        lines.extend(
            _format_post_summary_line(s, i + 1) for i, s in enumerate(summaries)
        )
        return AdminCommandResult(
            ok=True, action="posts",
            admin_text="\n".join(lines),
            data={"posts": [s.to_dict() for s in summaries]},
        )

    if command.action == "post":
        target = (command.target or "").strip()
        if not target:
            return AdminCommandResult(
                ok=False, action="post",
                admin_text="ขอ post_id ด้วยค่ะ",
                error="missing_target",
            )
        ctx = get_source_context(supabase, post_id=target)
        if ctx.page_post_id is None:
            return AdminCommandResult(
                ok=False, action="post",
                admin_text=f"ไม่พบโพสต์ id: {_safe_text(target)}",
                error="post_not_found",
            )
        summary = PagePostSummary(
            id=ctx.page_post_id, platform="facebook",
            page_id="", post_id=target,
            permalink_url=ctx.permalink_url, posted_at=ctx.posted_at or "",
            source_type=ctx.source_type,
            title=ctx.title or "",
            linked_web_codes=list(ctx.linked_web_codes),
            linked_tour_codes_real=list(ctx.linked_tour_codes_real),
        )
        return AdminCommandResult(
            ok=True, action="post",
            admin_text=_format_post_detail(summary),
            data={"post": ctx.to_dict()},
        )

    if command.action in {"mark_full", "mark_sold_out"}:
        status = "full" if command.action == "mark_full" else "sold_out"
        resolved = _resolve_page_post_target(supabase, command.target or "")
        if resolved.kind in {"ambiguous", "unknown"}:
            note = resolved.note or "ไม่เข้าใจค่าที่ส่งมา"
            return AdminCommandResult(
                ok=False, action=command.action,
                admin_text=(
                    f"{_safe_text(note)}\n"
                    f"ตัวอย่าง: {command.action} ap242455 หรือ "
                    f"{command.action} BCCKG27-HU หรือ "
                    f"{command.action} <post_id> ค่ะ"
                ),
                error="ambiguous_target" if resolved.kind == "ambiguous" else "target_not_found",
            )
        scope = "post" if resolved.kind == "post_id" else "tour"
        kwargs = {
            "scope": scope, "status": status,
            "marked_by": admin_user_id, "reason": command.reason,
        }
        if resolved.kind == "web_code":
            kwargs["web_code"] = resolved.value
        elif resolved.kind == "tour_code_real":
            kwargs["tour_code_real"] = resolved.value
        elif resolved.kind == "post_id":
            kwargs["page_post_id"] = (resolved.post_row or {}).get("id")
        override = mark_availability_override(supabase, **kwargs)
        return AdminCommandResult(
            ok=True, action=command.action, mutated=True,
            admin_text=(
                f"แจ้ง {_mark_status_text(status)} สำหรับ "
                f"{_safe_text(resolved.value or 'โพสต์')} แล้ว"
                + (f"\nเหตุผล: {_safe_text(command.reason)}" if command.reason else "")
            ),
            data={"override": override.to_dict()},
        )

    if command.action in {"clear_full", "clear_sold_out"}:
        resolved = _resolve_page_post_target(supabase, command.target or "")
        if resolved.kind in {"ambiguous", "unknown"}:
            note = resolved.note or "ไม่เข้าใจค่าที่ส่งมา"
            return AdminCommandResult(
                ok=False, action=command.action,
                admin_text=(
                    f"{_safe_text(note)}\n"
                    f"ตัวอย่าง: {command.action} ap242455 ค่ะ"
                ),
                error="ambiguous_target" if resolved.kind == "ambiguous" else "target_not_found",
            )
        scope = "post" if resolved.kind == "post_id" else "tour"
        kwargs = {"scope": scope, "cleared_by": admin_user_id}
        if resolved.kind == "web_code":
            kwargs["web_code"] = resolved.value
        elif resolved.kind == "tour_code_real":
            kwargs["tour_code_real"] = resolved.value
        elif resolved.kind == "post_id":
            kwargs["page_post_id"] = (resolved.post_row or {}).get("id")
        count = clear_availability_override(supabase, **kwargs)
        if count == 0:
            return AdminCommandResult(
                ok=True, action=command.action, mutated=False,
                admin_text=(
                    f"ไม่พบสถานะ override ที่ใช้งานสำหรับ "
                    f"{_safe_text(resolved.value or 'รายการนี้')} "
                    f"(อาจถูกเคลียร์ไปแล้วหรือหมดอายุ)"
                ),
                data={"cleared": 0},
            )
        return AdminCommandResult(
            ok=True, action=command.action, mutated=True,
            admin_text=(
                f"เคลียร์สถานะ {('full' if command.action == 'clear_full' else 'sold_out')} "
                f"สำหรับ {_safe_text(resolved.value or 'รายการนี้')} แล้ว"
            ),
            data={"cleared": count},
        )

    if command.action == "resume":
        case = _resolve_case(supabase, command.target or "", memory=memory)
        if not case:
            return AdminCommandResult(
                ok=False, action="resume",
                admin_text=f"ไม่พบเคสสำหรับ ID: {_safe_text(command.target or '-')}\nยังไม่ได้ resume บอท",
                error="case_not_found",
            )
        result = resume_bot_for_customer(
            supabase, psid=case.psid, resumed_by=admin_user_id,
            reason=command.reason,
        )
        case_name = _safe_text(case.display_name or case.psid_masked)
        return AdminCommandResult(
            ok=True, action="resume",
            admin_text=(
                f"เปิดบอทกลับให้เคส {case_name} แล้ว\n"
                f"ID: {result.psid_masked}\n"
                f"handoff ปิดแล้ว: {result.handoffs_closed}"
            ),
            data={"resume": result.to_dict()}, mutated=True,
        )

    return AdminCommandResult(
        ok=False, action=command.action,
        admin_text="ไม่เข้าใจคำสั่งนี้\n\n" + _help_text(),
        error="unsupported_command",
    )


__all__ = [
    "AdminCommand",
    "AdminCommandResult",
    "parse_admin_command",
    "handle_admin_command",
]
