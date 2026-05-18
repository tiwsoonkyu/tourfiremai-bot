# Current Dev Task

Task ID: `DEV-2026-05-19-004`
Status: `PENDING`
Assigned role: Claude Cowork Dev
Controller: Codex

## Task

Build the V2 Admin Handoff + Memory Control foundation.

This task is the next step after the paid-OCR provider abstraction was QA-cleared. The business priority is now operational reliability for real chat handling:

1. Admin must see which customer/case needs attention.
2. Admin must be able to pause the bot for a customer.
3. The bot must never interrupt while a human is handling the case.
4. Memory/selected tour state must remain inspectable and recoverable.

Do not build the full visual dashboard yet unless it is clearly small and testable. Prefer a backend/domain foundation and tests that a dashboard can safely use next.

## Context

TourFireMai V2 already has these foundations:

- `customers`
- `customer_memory`
- `conversations`
- `conversation_events`
- `conversation_turns`
- `offer_snapshots`
- `selected_tours`
- `handoffs`
- `bot_pauses`
- state machine states including `waiting_team` and `human_paused`
- `MemoryService` for customer memory, offer snapshots, and selected tour locks

The core customer pain from V1 testing was not only PDF accuracy. It was:

- bot forgets context after delay
- bot asks again after customer already selected a tour
- admin cannot take over cleanly
- bot may continue chatting while admin is active
- admin cannot easily see the customer name, latest intent, selected tour, or why handoff happened

This task should make those operational controls explicit and testable.

## Scope

You may modify V2 code, tests, and docs only.

Required work:

1. Inspect the current V2 memory, state machine, webhook, and Supabase migration foundations.
2. Add a small admin operations layer for case visibility and bot pause/resume.
3. Provide deterministic functions that a future dashboard or LINE command handler can call.
4. Add tests proving:
   - admin can pause a customer/conversation
   - paused customer becomes or remains `human_paused`
   - paused bot is silent / does not proceed with normal response flow
   - admin can resume a customer
   - case summary includes customer display name when available
   - case summary includes selected tour / latest offer state when available
   - open handoffs can be listed without exposing secrets or wholesale names

Suggested implementation shape:

- `v2/lib/admin_ops.py`
  - `AdminCaseSummary` dataclass
  - `list_admin_cases(...)`
  - `get_admin_case(psid_or_conversation_id, ...)`
  - `pause_bot_for_customer(psid, reason, paused_by, ttl_minutes, ...)`
  - `resume_bot_for_customer(psid, resumed_by, reason, ...)`
  - `record_handoff(...)` or reuse existing handoff rows if cleaner

- Optional if needed:
  - small helper functions in `v2/lib/memory.py`
  - small state-machine helper if current code cannot inspect pause state cleanly
  - docs describing the future dashboard contract

Do not create a production UI in this task unless it is trivial and fully tested. A JSON/view-model layer is enough for this task.

## Hard Constraints

- Do not touch V1 production behavior.
- Do not touch Make.com.
- Do not deploy anything.
- Do not change production Messenger webhook behavior.
- Do not print, write, or commit secrets.
- Do not make live OpenAI calls.
- Do not make live paid-provider calls.
- Do not introduce wholesale partner names into customer-facing output, prompts, logs, cassettes, reports, or tests.
- Do not weaken fee safety thresholds.
- Do not change PDF extraction behavior.
- Do not require real Supabase credentials for unit tests.
- Do not create a customer-facing auto-reply in this task.

## Required Tests

Add or update tests for:

1. Pause creates an active `bot_pauses` row and updates the active conversation to `human_paused`.
2. Resume marks the active pause as resumed and returns the conversation to a safe non-silent state.
3. Paused customers are treated as silent / no bot response path.
4. Admin case summary resolves:
   - customer name
   - PSID
   - conversation state
   - latest memory fields
   - selected tour lock if present
   - latest offer snapshot if present
   - open handoff if present
5. Open handoff queue can be listed deterministically.
6. No secrets or wholesale partner names are introduced.

Run the broad non-live V2 suite if feasible.

## Deliverables

Write:

`docs/tasks/DEV_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Use status:

`READY_FOR_QA`

## Dev Report Requirements

Include:

1. Status
2. Files changed
3. Root cause / business need
4. Summary of changes
5. Tests run
6. Risks / assumptions
7. What QA should verify
8. Next recommended step

## Stop Condition

After writing the Dev report and AGENT_STATUS, stop and wait for QA/Codex review.
