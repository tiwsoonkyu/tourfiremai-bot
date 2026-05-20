# V2 Departure Uniqueness Proposal

Sprint 5 Package I / DEV-2026-05-20-015 — supersedes the non-unique
`idx_dep_full_row` (migration 021) once the staging table is verified
duplicate-free.

## Goal

Promote the existing non-unique index on `tour_departures`:

```sql
CREATE INDEX IF NOT EXISTS idx_dep_full_row
  ON tour_departures (tour_id, departure_start, departure_end, bus);
```

into a true `UNIQUE` constraint so that the detail-page upsert seam in
`v2.scraper.detail_enrichment.upsert_departure_rows` is guaranteed
collision-free at the database layer (defense in depth on top of the
application-level idempotency key).

## Why this is gated

Migration 021 chose a non-unique index on purpose: at the time the
detail-row backfill was incomplete, and any historical scrape that
produced two rows for the same (tour, start, end, bus) tuple would
silently violate a fresh UNIQUE constraint and refuse to apply.

We must therefore prove on staging that the table has zero duplicate
groups under the proposed key before applying the UNIQUE migration.

## Audit gate

Before opening a UNIQUE-promotion migration:

1. Operator runs the SQL audit (no writes, read-only):

   ```sql
   SELECT
       tour_id,
       departure_start,
       departure_end,
       COALESCE(bus, 0) AS bus_key,
       COUNT(*) AS dupes
   FROM tour_departures
   WHERE departure_start IS NOT NULL
   GROUP BY tour_id, departure_start, departure_end, COALESCE(bus, 0)
   HAVING COUNT(*) > 1
   ORDER BY dupes DESC, tour_id, departure_start;
   ```

   The string is also exposed in code as
   `v2.tools.departure_duplicate_audit.DUPLICATE_AUDIT_SQL` so the
   operator can copy it from a single source.

2. Operator runs the Python audit against the same database (still a
   read):

   ```python
   from v2.tools.departure_duplicate_audit import find_duplicates
   result = find_duplicates(supabase)
   assert result.safe_for_unique_index
   ```

3. Only when both surfaces report `safe_for_unique_index == True` may
   Codex queue the UNIQUE-promotion migration. Otherwise the duplicate
   groups must be triaged manually first (no auto-deletes — duplicates
   may be legitimate "different airline rotation" rows that the new
   key still needs to distinguish).

## Proposed migration (NOT applied here)

The file `v2/supabase/migrations/_pending_023_departure_unique.sql.proposal`
contains the exact SQL block we plan to use once the audit returns
zero duplicates. The `.proposal` suffix keeps it out of any normal
`*.sql` migration glob, so no automation can accidentally apply it.

Key properties:

- Drops the old non-unique `idx_dep_full_row` and re-creates it as a
  partial UNIQUE index (only for rows with non-NULL `departure_start`,
  matching the application invariant).
- Uses `COALESCE(bus, 0)` so missing bus numbers collapse to the same
  bucket — matching the audit and the application's
  `idempotency_key` shape.
- Wrapped in a `BEGIN; ... COMMIT;` transaction so an unexpected
  duplicate causes a clean rollback rather than a half-applied schema.
- Includes a `CREATE UNIQUE INDEX CONCURRENTLY` variant in comments so
  the operator can choose the non-blocking path on a hot staging
  table.

## Out of scope for DEV-2026-05-20-015

- No data deletion.
- No automatic migration application.
- No UNIQUE constraint added to the live `tour_departures` row count.
- No production reads or writes.
