"""
v2.scraper.save_fees — Persist ExtractionResult to tour_fees table.

Step 9 of fee pipeline brief. Idempotent on (tour_id) via existing unique
index `idx_fees_tour`.
"""

from __future__ import annotations

import logging
from typing import Optional

from .extract_fees import ExtractionResult

logger = logging.getLogger("v2.scraper.save_fees")


def upsert_tour_fees(
    supabase,
    *,
    tour_id: str,
    tour_code_real: Optional[str],
    pdf_url: str,
    pdf_hash: str,
    result: ExtractionResult,
) -> dict:
    """
    Upsert one extraction result into tour_fees. Returns the resulting row.

    If a row exists for this tour_id, updates only if:
      - pdf_hash changed (new PDF version), OR
      - extraction_confidence improved
    Otherwise no-op + returns existing row.
    """
    row = result.to_db_row(
        tour_id=tour_id,
        tour_code_real=tour_code_real or "",
        pdf_url=pdf_url,
        pdf_hash=pdf_hash,
    )

    existing = supabase.table("tour_fees").select_one({"tour_id": tour_id})
    if existing:
        same_hash = existing.get("pdf_hash") == pdf_hash
        better = result.extraction_confidence > (existing.get("extraction_confidence") or 0)
        if same_hash and not better:
            logger.info("tour_fees row exists for tour_id=%s with same hash + equal/better confidence — no-op", tour_id)
            return existing
        # Update
        supabase.table("tour_fees").update({"tour_id": tour_id}, row)
        return supabase.table("tour_fees").select_one({"tour_id": tour_id})

    return supabase.table("tour_fees").insert(row)
