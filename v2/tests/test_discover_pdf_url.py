"""Sprint 3 R2 test: PDF URL discovery — HTML parsing."""

import pytest
from v2.scraper.discover_pdf_url import (
    discover_pdf_url, parse_pdf_url_from_html, PdfDiscoveryResult,
)


class FakeHttpClient:
    def __init__(self, status: int, text: str = ""):
        self.status = status
        self.text_body = text

    def get(self, url, timeout=30):
        class R:
            status_code = self.status
            text = self.text_body
        return R()


class TestParseHtml:
    def test_finds_pdf_anchor(self):
        html = '<a href="/files/tour123.pdf">Download</a>'
        assert parse_pdf_url_from_html(html) == "https://www.tourfiremai.com/files/tour123.pdf"

    def test_finds_absolute_url(self):
        html = '<a href="https://example.com/x.pdf">PDF</a>'
        assert parse_pdf_url_from_html(html) == "https://example.com/x.pdf"

    def test_protocol_relative(self):
        html = '<a href="//cdn.x.com/y.pdf">PDF</a>'
        assert parse_pdf_url_from_html(html) == "https://cdn.x.com/y.pdf"

    def test_query_string_pdf(self):
        html = '<a href="/download.php?file=x.pdf&id=1">D</a>'
        out = parse_pdf_url_from_html(html)
        assert out is not None
        assert ".pdf" in out

    def test_no_pdf_returns_none(self):
        assert parse_pdf_url_from_html("<html>no pdf</html>") is None

    def test_empty(self):
        assert parse_pdf_url_from_html("") is None


class TestDiscoverPdfUrl:
    def test_db_short_circuit(self, supabase, make_tour):
        make_tour(web_code="ap111", name="X", price=10000)
        # Patch pdf_url on the row
        supabase.table("tours_canonical").update(
            {"web_code": "ap111"}, {"pdf_url": "https://x/y.pdf"}
        )
        result = discover_pdf_url("ap111", supabase=supabase, prefer_db=True,
                                    http_client=FakeHttpClient(404))
        assert result.pdf_url == "https://x/y.pdf"
        assert result.found_in == "db_column"

    def test_html_scrape(self, supabase, make_tour):
        make_tour(web_code="ap222", name="X", price=10000)
        html = '<html><a href="/programs/ap222.pdf">PDF</a></html>'
        result = discover_pdf_url("ap222", supabase=supabase,
                                    http_client=FakeHttpClient(200, html))
        assert result.pdf_url is not None
        assert ".pdf" in result.pdf_url
        assert result.found_in == "detail_html"

    def test_no_pdf_anywhere(self, supabase, make_tour):
        make_tour(web_code="ap333", name="X", price=10000)
        result = discover_pdf_url("ap333", supabase=supabase,
                                    http_client=FakeHttpClient(200, "<html>nothing</html>"))
        assert result.pdf_url is None
        assert result.found_in == "not_found"

    def test_http_error(self, supabase, make_tour):
        make_tour(web_code="ap444", name="X", price=10000)
        result = discover_pdf_url("ap444", supabase=supabase,
                                    http_client=FakeHttpClient(500))
        assert result.pdf_url is None
        assert "http_status" in result.notes
