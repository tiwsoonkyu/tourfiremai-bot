# CURRENT DEV TASK

## Task ID
DEV-2026-05-19-011

## Title
Sprint 5 Package E - Admin-Only Staging Preflight, Migration Readiness, and Operator Test Prep

## Status
PENDING

## Assigned Role
Claude Dev

## Controller
Codex

## Source of Truth

Use GitHub repo as source of truth:

- Repo: `github.com/tiwsoonkyu/tourfiremai-bot`
- Branch: `v2/s4-followup-vision-ondemand`

If your local Cowork workspace lacks git/source files, differs from the GitHub branch, or cannot inspect the changed files, stop and report:

`BLOCKED: source-of-truth repo unavailable`

Do not invent scope from chat memory. This file is the approved scope.

## Context

Sprint 5 Package D is controller-accepted and QA-cleared:

- Admin-only test-mode gate exists.
- V2 webhook can filter non-allowlisted PSIDs before processing.
- Admin dashboard runtime config endpoint exists and must not expose secrets.
- Admin-only real chat runbook exists.

The next business goal is to prepare for a safe admin-only staging test with Tiw/admin accounts only.

This package should make it easy for Codex/Tiw to verify readiness before touching any Meta webhook settings.

## Goal

Create a staging preflight package that validates readiness for admin-only real-chat testing without using production traffic, live paid providers, or printing secrets.

This task is not a production launch and must not connect the production page webhook.

## Scope

Implement all subtasks below as one integrated Dev package. Do not stop after each small subtask unless a P0 risk appears.

### 1. Staging Preflight CLI or Test Module

Add a lightweight V2-only preflight entrypoint that can be run locally before admin-only staging testing.

It should verify, without printing secret values:

- Required staging env vars are present or missing.
- Admin-only mode is explicitly enabled.
- Admin/test PSID allow-list is configured.
- Dashboard admin token is configured.
- LINE admin allow-list is configured.
- Staging Supabase URL is configured.
- Staging Redis URL is configured, if runtime requires it.
- Meta staging app secret and verify token are configured.
- OpenAI/OCR/live provider keys are not required for this admin-only preflight.

Preferred location:

- `v2/tools/admin_only_preflight.py`

If a better existing location is found, explain why in the Dev report.

The output must be a structured dictionary/JSON-like report with statuses such as:

- `ok`
- `missing`
- `disabled`
- `not_required`

Never print raw tokens, raw PSIDs, passwords, service keys, or webhook secrets.

### 2. Migration 020 Readiness Check

Add a safe, non-network migration readiness check that inspects:

- `v2/supabase/migrations/20260519_020_page_post_intelligence.sql`

It should assert the migration defines:

- `page_posts`
- `page_post_tour_links`
- `tour_availability_overrides`
- RLS enabled for all three tables
- anon denied for all three tables
- service_role policies for all three tables

Do not apply the migration from Claude Dev.

### 3. Runbook Update

Update `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md` with a short "Before Meta Webhook Change" checklist:

1. Run preflight.
2. Confirm migration 020 has been applied by Codex/Tiw.
3. Confirm admin allow-list contains only Tiw/admin PSIDs.
4. Confirm V2 customer outbound replies are still disabled.
5. Confirm dashboard token is known only to admin.
6. Confirm rollback steps are understood.

Add the exact local command for the preflight tool.

### 4. Tests

Add targeted tests for:

- Preflight redacts all secret-like values.
- Missing env vars are reported as missing, not printed.
- Admin-only mode disabled returns a non-ready status.
- Admin-only mode enabled + required variables returns ready.
- Migration 020 readiness check passes on the current SQL file.
- Migration checker fails on a synthetic SQL string missing RLS or anon-deny.

Run:

- Targeted new tests.
- Existing admin/runtime/webhook tests if relevant.
- Broad non-live V2 suite when feasible.

## Out of Scope

Do not:

- Touch V1 production behavior.
- Reactivate or modify Make.com.
- Change production Messenger webhook settings.
- Deploy anything.
- Apply Supabase migrations.
- Read, print, or commit secrets.
- Call Meta Graph API.
- Call LINE API.
- Call OpenAI.
- Call OCR/document providers.
- Enable customer-facing outbound replies.
- Change sales recommendation logic.
- Change PDF fee extraction thresholds.

## Expected Deliverables

- V2-only code/tests/docs.
- Updated `docs/S5_ADMIN_ONLY_REAL_CHAT_RUNBOOK.md`.
- `docs/tasks/DEV_REPORT_CURRENT.md`.
- `docs/tasks/AGENT_STATUS.json`.

## Required Dev Report

Write `docs/tasks/DEV_REPORT_CURRENT.md` with:

1. Status
2. Files changed
3. Summary of changes
4. Tests run
5. Risks / assumptions
6. What QA should verify
7. Next recommended step

Then stop for QA.
