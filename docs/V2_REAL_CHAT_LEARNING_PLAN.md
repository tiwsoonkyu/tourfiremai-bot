# V2 Real Chat Learning Plan

Last updated: 2026-05-22

## Goal

Make TourFireMai V2 improve from real Messenger conversations without depending on V1 and without letting the model silently train itself on noisy customer data.

The bot should learn how real customers ask, how admins answer, which replies lead to continued conversation, and where the bot should hand off.

## Current Problem

The bot can already receive Messenger events and call the V2 response path, but when verified tour data is missing or unsafe, it still falls back to short generic replies. This creates the feeling that the bot does not understand the conversation.

## Design Principle

Learning must be supervised:

1. Capture real chat events.
2. Redact private data.
3. Let admins mark useful examples.
4. Convert approved examples into test cases, prompt examples, intent rules, or tool fixes.
5. QA before production behavior changes.

Do not fine-tune or auto-update sales behavior directly from raw chats.

## Data To Capture

For every inbound/outbound turn:

- conversation_id / psid hash
- source_type: page_post, ad, organic, unknown
- customer text
- bot reply
- admin reply if admin takes over
- detected intent
- selected_tour / selected_departure if any
- handoff reason
- outcome: continued, admin_takeover, booking_ready, lost, unknown
- error flags: repeated_question, no_tour_found, missing_fee, low_confidence, duplicate_reply

## Proposed Tables

### conversation_learning_samples

Approved examples that can be reused for evaluation or prompt examples.

Fields:

- id
- conversation_id
- sample_type: good_admin_reply, bad_bot_reply, intent_example, handoff_example
- customer_message
- admin_reply
- bot_reply
- expected_behavior
- tags
- approved_by
- approved_at

### bot_failure_cases

Failures automatically detected or manually flagged.

Fields:

- id
- conversation_id
- failure_type
- customer_message
- bot_reply
- context_snapshot
- severity
- resolved_at
- resolution_note

### intent_patterns

Human-approved examples for deterministic intent matching.

Fields:

- id
- intent_name
- example_text
- normalized_text
- confidence_hint
- active

### eval_cases

Stable regression tests generated from real chat.

Fields:

- id
- scenario_name
- input_turns
- expected_reply_policy
- expected_tool_calls
- forbidden_outputs
- active

## Dashboard Actions

Admin should be able to click:

- "Bot answer bad"
- "Good admin reply, use as example"
- "Wrong intent"
- "Should handoff"
- "Tour full / unavailable"
- "Customer still interested"
- "Booking ready"

These actions create learning samples, not automatic production changes.

## First Implementation Task

Task proposal: `DEV-2026-05-22-017 Conversation Learning Intake`

Scope:

1. Add schema for `conversation_learning_samples`, `bot_failure_cases`, `intent_patterns`, and `eval_cases`.
2. Add service functions to create learning samples from existing conversation events.
3. Add dashboard-safe read/write functions for admin flags.
4. Add unit tests for PII redaction, wholesale-name scrubbing, and no direct production behavior change.
5. Add a small eval exporter that turns approved samples into JSONL test fixtures.

Out of scope:

- Fine-tuning.
- Auto-learning without admin approval.
- Reading old V1 data.
- Touching production webhook.

## Metrics

Track weekly:

- duplicate_reply_rate
- repeated_question_rate
- safe_top3_success_rate
- admin_takeover_rate
- handoff_reason distribution
- customer_reply_after_bot_rate
- booking_ready_rate

## Near-Term Bot Quality Fixes

Before using learning data at scale, V2 should also:

1. Avoid claiming verified tours exist when the search result contains no safe customer-visible rows.
2. Never show fixture/test rows to customers.
3. Keep known inputs in the LLM payload so it does not ask for budget/month again.
4. If country + budget + month are already known but no verified tours are available, say that the system is checking verified options and hand off or ask the team, rather than looping.

