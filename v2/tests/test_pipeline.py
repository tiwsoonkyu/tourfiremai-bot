"""Sprint 3 R2 test: full offline pipeline (mock LLM + InMemory supabase)."""

import pytest

pdfplumber = pytest.importorskip("pdfplumber")
reportlab = pytest.importorskip("reportlab.pdfgen.canvas")

from v2.scraper.run_fee_pipeline import run_for_tour, PipelineResult
from v2.scraper.save_fees import upsert_tour_fees
from v2.scraper.extract_fees import ExtractionResult
from v2.lib.llm import MockLLMClient
from v2.tests.fixtures.synthetic_pdf import build_synthetic_fee_pdf


class TestSaveFees:
    def test_insert_new_row(self, supabase, make_tour):
        tour = make_tour(web_code="ap999", name="X", price=10000, tour_code_real="X-1")
        r = ExtractionResult(
            tip_amount=1500, single_supplement=5500, deposit_amount=10000,
            visa_status="exempt", extraction_confidence=0.9,
            extraction_method="pdfplumber+regex",
        )
        row = upsert_tour_fees(
            supabase, tour_id=tour["id"], tour_code_real="X-1",
            pdf_url="https://x.com/y.pdf", pdf_hash="abc123", result=r,
        )
        assert row["tip_amount"] == 1500
        assert row["visa_status"] == "exempt"

    def test_no_op_on_same_hash(self, supabase, make_tour):
        tour = make_tour(web_code="ap888", name="X", price=10000, tour_code_real="X-2")
        r = ExtractionResult(tip_amount=1000, single_supplement=2000,
                              deposit_amount=5000, visa_status="exempt",
                              extraction_confidence=0.8)
        # First insert
        upsert_tour_fees(supabase, tour_id=tour["id"], tour_code_real="X-2",
                         pdf_url="https://x", pdf_hash="hash1", result=r)
        # Try update with lower confidence + same hash → no-op
        r2 = ExtractionResult(tip_amount=999, single_supplement=2000,
                               deposit_amount=5000, visa_status="exempt",
                               extraction_confidence=0.5)
        out = upsert_tour_fees(supabase, tour_id=tour["id"], tour_code_real="X-2",
                                pdf_url="https://x", pdf_hash="hash1", result=r2)
        assert out["tip_amount"] == 1000  # original value preserved


class TestRunForTour:
    @pytest.fixture
    def tour_with_pdf(self, supabase, make_tour, tmp_path):
        tour = make_tour(web_code="ap777", name="Test", price=20000, tour_code_real="T-1")
        pdf = build_synthetic_fee_pdf(str(tmp_path / "f.pdf"))
        # Patch pdf_url so discover step short-circuits to DB
        supabase.table("tours_canonical").update(
            {"web_code": "ap777"}, {"pdf_url": f"file://{pdf}"}
        )
        return tour, pdf

    def test_pipeline_runs_end_to_end(self, supabase, tour_with_pdf, tmp_path):
        tour, pdf = tour_with_pdf
        llm = MockLLMClient()

        # Need to patch download_pdf to use file:// scheme; easier to mock
        from unittest.mock import patch
        from v2.scraper.run_fee_pipeline import run_for_tour
        from v2.scraper.download_pdf import PDFArtifact
        import hashlib

        # Build artifact pointing to our existing local PDF
        with open(pdf, "rb") as f: data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        fake_artifact = PDFArtifact(
            url=f"file://{pdf}", local_path=pdf, sha256=sha,
            size_bytes=len(data), fetched_at=0,
        )

        with patch("v2.scraper.run_fee_pipeline.download_pdf", return_value=fake_artifact):
            row_in = supabase.table("tours_canonical").select_one({"id": tour["id"]})
            result = run_for_tour(
                tour_row=row_in, supabase=supabase, llm=llm,
                cache_dir=str(tmp_path / "cache"), skip_vision=True,
            )

        assert result.pdf_url == f"file://{pdf}"
        assert result.pdf_kind in ("text", "mixed")
        assert result.saved is True or result.extraction is not None
        # Synthetic PDF has tip/visa/single/deposit + we should NOT need handoff
        # but visa_status detection on "visa fee 1650" may not trigger 'required';
        # accept either outcome (handoff or not) — what we verify is the row was saved
        fees_row = supabase.table("tour_fees").select_one({"tour_id": tour["id"]})
        assert fees_row is not None
        assert fees_row.get("tip_amount") == 1500
