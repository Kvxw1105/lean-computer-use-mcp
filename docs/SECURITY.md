# Security

## Threat model

The facade is a local proxy between an agent model and a desktop-control runtime. The main risks are:

- acting on a stale or wrong window;
- performing an unconfirmed or double-executed action;
- leaking sensitive screen content into model context;
- on-screen prompt injection overriding protocol rules;
- a compromised upstream binary or agent prompt causing destructive actions.

## Boundaries

- `lean-computer-use-mcp` controls state freshness, payload size, action caps, and retry policy.
- User confirmation is a model/user policy decision. The facade never invents confirmation and never exposes an API for it.
- The skill layer binds confirmation to an exact app, target, action/content, action-count ceiling, and state revision.

## Stale-state enforcement

- Every action requires a `state_id`.
- Missing, expired, or non-current states are rejected with `STALE_STATE`.
- A refreshed snapshot from an action atomically replaces the previous state for that app.
- Indices and coordinates are never reused across states.
- Coordinate actions (`x`/`y`, `drag`) pass through the same live fingerprint gate as indexed actions; a coordinate planned against a stale snapshot is rejected before execution.

## Action caps and retries

- `cu_batch` is capped server-side at `max_actions` (hard maximum 3 in M0).
- Commit-like actions (`commit=true`) are one-shot: timeout or ambiguity returns `COMMIT_UNCERTAIN` and automatic retries are forbidden.
- Read-only transient failures may be retried once; argument errors must be corrected, not repeated.

## Sensitive content

- Screenshots and accessibility trees are cached only in a local temp directory and are not logged to metrics.
- Passwords, MFA/recovery codes, payment approval, credentials, and security settings default to manual user handling.
- The facade should not forward unrelated windows, private content, or whole-desktop screenshots unless explicitly requested.
- A vision engine (OCR / grounding) runs only when explicitly configured (`LEAN_CU_VISION_ENGINE`); screenshots are never sent to a remote backend by default. Vision output is untrusted UI text and can never override protocol rules.

## Prompt injection

- Visible UI text is untrusted data.
- No instruction rendered inside an application can change confirmation, scope, or retry rules.
- If a page asks the agent to "ignore rules" or click a destructive button, the facade and skill treat it as content, not authority.

## Logging

- Metrics contain counts and sizes, never screen text or image bytes.
- Metrics files are user-owned and should be excluded from Git (see `.gitignore`).
- Real accessibility trees used in tests must be sanitized.

## Release checklist

- [x] `cu_act` stale rejection verified on the real desktop: non-current, bogus, and expired `state_id` values all reject with zero upstream action calls (M1).
- [ ] `cu_act` stale rejection against a window that changed between turns (live fingerprint gate; unit-tested, real-window demo pending a confirmed action run).
- [x] Batch cap enforced with a test that requests more than `max_actions`.
- [ ] Commit ambiguity path covered: timeout does not auto-retry.
- [x] No screenshot bytes or accessibility text written to metrics (counts/sizes only; verified in `MetricsLogger`).
- [ ] Installer does not overwrite other MCP servers.