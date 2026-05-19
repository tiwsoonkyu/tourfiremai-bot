# Current Dev Task

Task ID: `DEV-2026-05-19-005`
Status: `PENDING`
Assigned role: Claude Cowork Dev
Controller: Codex

## Task

Wire the V2 Admin Handoff + Memory Control foundation into a deterministic LINE admin command handler.

This task follows the QA-cleared `DEV-2026-05-19-004` admin_ops foundation. The business priority is operational reliability during live chat handling: staff must be able to see cases, pause the bot, resume the bot, and inspect a case from the staff LINE channel without the bot interrupting customers.

Do not implement a visual dashboard in this task. Build the backend command parser/handler layer that a future LINE webhook adapter can call.

## Context

`DEV-2026-05-19-004` added:

- `v2/lib/admin_ops.py`
- `AdminCaseSummary`
- `pause_bot_for_customer(...)`
- `resume_bot_for_customer(...)`
- `is_bot_paused_for(...)`
- `get_admin_case(...)`
- `list_admin_cases(...)`
- `list_open_handoffs(...)`
- `record_handoff(...)`

QA verdict for that task: `GO`.

Current product invariant:

> When a human/admin is handling a customer, the bot must not interrupt.

The next practical step is a LINE admin command handler that calls `admin_ops` safely. This is not the final LINE Messaging API integration yet; it is the deterministic core that can be wrapped by an actual LINE webhook later.

## Scope

You may modify V2 code, tests, and docs only.

Required work:

1. Inspect `v2/lib/admin_ops.py`, `v2/lib/orchestrator.py`, current LINE/notification helper conventions, and existing tests.
2. Add a deterministic admin command parser and handler layer.
3. The handler must be pure/backend-safe:
   - no live LINE send
   - no network calls
   - no customer-facing Messenger replies
   - no secrets
4. The handler should return a structured result plus safe Thai admin-facing text that a future LINE adapter can send.
5. The handler must call `admin_ops` for case listing, case detail, pause, and resume.

Suggested implementation shape:

- `v2/lib/admin_command_handler.py`
  - `AdminCommand`
  - `AdminCommandResult`
  - `parse_admin_command(text: str) -> AdminCommand`
  - `handle_admin_command(command_or_text, supabase, *, admin_user_id, memory=None, now=None) -> AdminCommandResult`
  - Supported commands:
    - `cases`
    - `cases paused`
    - `handoffs`
    - `case <psid_or_conversation_id>`
    - `pause <psid_or_conversation_id> [reason...]`
    - `resume <psid_or_conversation_id> [reason...]`
    - `help`

If you find a cleaner shape, use it and explain the tradeoff.

## Required Behaviors

1. `cases` lists a short safe queue of current open admin cases.
2. `cases paused` lists paused/human-handled cases only.
3. `handoffs` lists open handoffs.
4. `case <id>` returns a safe case detail using `get_admin_case(...)`.
5. `pause <id> [reason]` pauses the customer using `pause_bot_for_customer(...)`.
6. `resume <id> [reason]` resumes the customer using `resume_bot_for_customer(...)`.
7. `help` returns a short command list.
8. Unknown/ambiguous commands return a safe help message.
9. Output text must be safe for a staff LINE group:
   - include customer display name if available
   - include masked PSID or case id where useful
   - include conversation state
   - include selected tour / latest offer summary when available
   - do not expose secrets
   - do not expose wholesale partner names
10. If the target customer cannot be found, return a clear admin-facing error and do not create a fake pause.

## Hard Constraints

- Do not touch V1 production behavior.
- Do not touch Make.com.
- Do not deploy anything.
- Do not change production Messenger webhook behavior.
- Do not make live LINE API calls.
- Do not make live OpenAI calls.
- Do not make live paid-provider calls.
- Do not print, write, or commit secrets.
- Do not introduce wholesale partner names into source, prompts, logs, cassettes, reports, or customer/admin output.
- Do not weaken fee safety thresholds.
- Do not change PDF extraction behavior.
- Do not require real Supabase credentials for unit tests.
- Do not create a customer-facing auto-reply in this task.

## Required Tests

Add or update tests for:

1. Parser recognizes `cases`, `cases paused`, `handoffs`, `case`, `pause`, `resume`, and `help`.
2. Parser handles Thai/English whitespace and unknown commands safely.
3. `cases` result calls/list-renders admin cases safely.
4. `handoffs` result calls/list-renders open handoffs safely.
5. `case <id>` renders customer name, state, selected tour/latest offer/handoff when present.
6. `pause <id>` calls `pause_bot_for_customer` and returns a paused confirmation.
7. `resume <id>` calls `resume_bot_for_customer` and returns a resumed confirmation.
8. Missing target returns an admin-safe error and does not mutate pause state.
9. Output text masks PSIDs where appropriate and contains no secret patterns.
10. Output text contains no wholesale partner names, including hostile fixture strings.

Run the broad non-live V2 suite if feasible.

## Deliverable

Write:

`docs/tasks/DEV_REPORT_CURRENT.md`

Update:

`docs/tasks/AGENT_STATUS.json`

Commit and push your changes to:

`v2/s4-followup-vision-ondemand`

## Dev Report Format

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

After writing the report/status and pushing, stop. Do not proceed to QA yourself.
