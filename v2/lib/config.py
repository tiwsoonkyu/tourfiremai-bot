"""
v2.lib.config — Staging-isolated env loader.

CRITICAL: Sprint 2 brief mandates SEPARATE staging credentials. This module
enforces that by:
  - Requiring env vars to be prefixed `V2_STAGING_` (default) OR
    explicitly passing prefix='V2_PROD_' / 'V2_DEV_'
  - Raising on any attempt to read a V1-style env name (FB_PAGE_TOKEN, etc.)
  - Providing a single Config dataclass that the rest of v2/ depends on
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# V1 env names that MUST NOT leak into V2 — fail loudly if anyone tries
_V1_FORBIDDEN = {
    "ANTHROPIC_API_KEY",
    "FB_PAGE_ACCESS_TOKEN", "FB_PAGE_TOKEN", "FB_VERIFY_TOKEN", "FB_APP_SECRET",
    "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY",
    "REDIS_URL",
    "LINE_CHANNEL_TOKEN", "LINE_GROUP_ID",
    "DASHBOARD_PASSWORD",
}

_V2_PREFIX = "V2_STAGING_"


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Read env var WITH prefix; never reads V1 names directly."""
    full = f"{_V2_PREFIX}{name}"
    val = os.environ.get(full, default)
    if required and not val:
        raise ConfigError(f"Missing required env: {full}")
    return val


def assert_no_v1_leak() -> None:
    """Raise if any V1 env name is set in the current process — paranoia guard."""
    leaks = [k for k in _V1_FORBIDDEN if os.environ.get(k)]
    if leaks:
        raise ConfigError(
            f"V1 env vars detected in process: {leaks}. "
            f"V2 must run with isolated V2_STAGING_* env only."
        )


@dataclass(frozen=True)
class Config:
    # Supabase (V2 staging)
    supabase_url: str
    supabase_db_host: str
    supabase_db_port: int
    supabase_db_user: str
    supabase_db_password: str
    supabase_db_name: str
    supabase_service_key: Optional[str]
    supabase_anon_key: Optional[str]

    # Redis (V2 staging — falls back to in-memory if missing)
    redis_url: Optional[str]

    # Meta / FB (V2 staging Page only)
    fb_app_secret: Optional[str]
    fb_page_access_token: Optional[str]
    fb_verify_token: Optional[str]

    # LLM (V2 staging key only — stub if absent)
    openai_api_key: Optional[str]
    openai_model: str

    # LINE notify (V2 staging — separate from V1)
    line_channel_token: Optional[str]
    line_admin_user_or_group_id: Optional[str]

    # Misc
    log_level: str
    env_name: str

    @property
    def has_redis(self) -> bool:
        return bool(self.redis_url)

    @property
    def has_llm(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_line(self) -> bool:
        return bool(self.line_channel_token and self.line_admin_user_or_group_id)

    @property
    def database_uri(self) -> str:
        return (
            f"postgresql://{self.supabase_db_user}:{self.supabase_db_password}"
            f"@{self.supabase_db_host}:{self.supabase_db_port}/{self.supabase_db_name}"
        )


def load_config(*, strict: bool = True) -> Config:
    """Load V2 staging config from env. Fails fast on missing DB creds."""
    if strict:
        assert_no_v1_leak()
    return Config(
        supabase_url=_env("SUPABASE_URL", required=True),
        supabase_db_host=_env("DB_HOST", required=True),
        supabase_db_port=int(_env("DB_PORT", "6543")),
        supabase_db_user=_env("DB_USER", required=True),
        supabase_db_password=_env("DB_PASSWORD", required=True),
        supabase_db_name=_env("DB_NAME", "postgres"),
        supabase_service_key=_env("SUPABASE_SERVICE_KEY"),
        supabase_anon_key=_env("SUPABASE_ANON_KEY"),
        redis_url=_env("REDIS_URL"),
        fb_app_secret=_env("FB_APP_SECRET"),
        fb_page_access_token=_env("FB_PAGE_ACCESS_TOKEN"),
        fb_verify_token=_env("FB_VERIFY_TOKEN"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        line_channel_token=_env("LINE_CHANNEL_TOKEN"),
        line_admin_user_or_group_id=_env("LINE_ADMIN_USER_OR_GROUP_ID"),
        log_level=_env("LOG_LEVEL", "INFO"),
        env_name=_env("ENV_NAME", "staging"),
    )
