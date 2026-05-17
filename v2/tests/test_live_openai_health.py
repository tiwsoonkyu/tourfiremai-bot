"""Sprint 4 live test: ping each tier of OpenAI to verify model availability.

Skipped automatically without V2_STAGING_OPENAI_API_KEY.
"""

import os
import pytest

api_key_set = bool(os.environ.get("V2_STAGING_OPENAI_API_KEY"))
pytestmark = pytest.mark.skipif(not api_key_set,
                                 reason="V2_STAGING_OPENAI_API_KEY not set")


@pytest.fixture(scope="module")
def live_client():
    os.environ.setdefault("V2_STAGING_SUPABASE_URL", "https://test.supabase.co")
    os.environ.setdefault("V2_STAGING_DB_HOST", "x")
    os.environ.setdefault("V2_STAGING_DB_USER", "x")
    os.environ.setdefault("V2_STAGING_DB_PASSWORD", "x")
    os.environ.setdefault("V2_STAGING_FB_APP_SECRET", "dummy")
    os.environ["V2_STAGING_OPENAI_TEST_MODE"] = "live"
    from v2.lib.config import load_config
    from v2.lib.llm import OpenAILLMClient
    cfg = load_config(strict=False)
    return OpenAILLMClient(cfg)


def test_response_tier_responds(live_client):
    rsp = live_client.chat(
        tier="response",
        messages=[{"role": "system", "content": "Reply with exactly: OK"},
                  {"role": "user", "content": "ping"}],
        max_tokens=10, temperature=0.0,
    )
    assert rsp.text
    assert rsp.usage.tokens_in > 0
    assert rsp.usage.model_used  # at least one model from ladder responded


def test_fast_tier_responds(live_client):
    rsp = live_client.chat(
        tier="fast",
        messages=[{"role": "user", "content": "say hi"}],
        max_tokens=10,
    )
    assert rsp.usage.tokens_in > 0


def test_vision_tier_responds_with_tiny_image(live_client):
    # 1x1 PNG (8 bytes header + minimal)
    one_pixel_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    rsp = live_client.vision(
        messages=[{"role": "user", "content": "describe this image briefly"}],
        image_bytes=one_pixel_png, max_tokens=30,
    )
    assert rsp.text
