# TourFireMai V2 — Sprint 1 Foundation

This directory contains the **V2** rewrite of the TourFireMai bot.

V1 (in the repo root, `app.py`) is **untouched** during Sprint 1.

## Layout

```
v2/
├── lib/
│   ├── country.py       # Country normalization + city → country
│   ├── tour_codes.py    # web_code / tour_code_real / airline separation
│   ├── idempotency.py   # meta_message_id + Redis dedup + per-PSID lock
│   └── memory.py        # 3-layer memory + offer snapshot + selected_tour
├── scraper/
│   └── scrape_tours.py  # tourfiremai.com → tours_canonical
├── supabase/
│   └── migrations/      # 16 SQL migration files
├── tests/
│   ├── conftest.py      # InMemoryRedis + InMemorySupabase fakes
│   └── test_*.py        # pytest suite (6 files)
├── requirements.txt
└── pytest.ini
```

## Running tests

```bash
cd /path/to/tourfiremai-bot
python -m venv .venv && . .venv/bin/activate
pip install -r v2/requirements-dev.txt
PYTHONPATH=. pytest v2/tests -v
```

## Apply migrations

```bash
# Replace <password> with V2 staging DB password
PGPASSWORD='<password>' psql \
  -h aws-0-ap-southeast-1.pooler.supabase.com -p 6543 \
  -U postgres.mbcihtcdwfofagkxphcu -d postgres \
  -f v2/supabase/migrations/20260517_001_customers.sql

# Apply in order 001 → 016
for f in v2/supabase/migrations/*.sql; do
  PGPASSWORD='<password>' psql -h ... -f "$f"
done
```

## Sprint 1 scope (this branch)

✅ Schema migrations
✅ Tour scraper (no live network in tests)
✅ Memory layer + offer snapshot
✅ Idempotency primitives
✅ Country/tour code normalization
✅ Unit tests

❌ Out of scope (Sprint 2+):
- Live Meta webhook
- LLM call orchestration
- PDF fee extraction (Sprint 4)
- Production deploy
