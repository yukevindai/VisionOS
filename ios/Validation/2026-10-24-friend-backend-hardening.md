# Trusted-friend backend hardening — October 24, 2026

This slice adds invitation attempt limits and bounded friendship listing. It does not implement or modify the teammate's ScreenModel, Target Selector or Gemini pipeline. Native Swift/media, R1 capture/audio and H1 hardware code were unchanged. Existing work was preserved.

## Delivered behavior

Invitation creation and redemption now charge an authenticated account's durable budget in a separate internal Convex mutation before action-level format validation or invitation writes. Invalid, expired, unknown and wrong-account codes therefore consume an attempt even when the subsequent action fails. The counter derives its account and time on the server; callers cannot supply them. Accounts must already exist through the ordinary authenticated setup path.

Initial server policies in `convex/inviteLimits.ts`:

| Operation | Initial burst | Refill |
|---|---:|---|
| Create invitation | 5 | One attempt per 60 seconds |
| Redeem invitation | 10 | One attempt per 10 seconds |

The arrival-time algorithm stores at most one `inviteAttemptBudgets` row per account/operation, not a row for every request or time window. Denial returns a `ConvexError` data object with `code: "RATE_LIMITED"` and `retryAfterMs`. These are initial application policies, not measured production capacity or a global anti-abuse guarantee. Account creation, infrastructure request limits and multi-account abuse remain separate concerns. Invalid Convex argument envelopes (for example an invalid typed bound-helper ID) are rejected before the action and do not reach the budget; accepted transport values for code/lifetime are parsed from `unknown` only after charging.

The existing 16-active-invitation cap, single use, bound account enforcement, explicit owner approval for unbound invites, directional friendship, and opt-in notifications remain. Revocation, End, Stop Sharing and other access-closing operations have no invitation quota. A regression verifies revocation and notification removal still succeed after quota exhaustion.

`friends.list` now requires `direction` (`owned` or `helping`), `state` (`accepted` or `pending`), and `paginationOpts`. Each page returns `{ friendship, counterpart: { id, displayName } | null }` rows. New indexes include authenticated owner/helper plus state, excluding revoked history before scanning. `friends.pendingInvites` separately returns only live approval requests with invite ID, expiry and requester display identity; codes and hashes are never projected.

Both APIs accept requested page sizes 1–50 and apply server-owned read budgets of 50 rows and 64 KiB to the underlying pagination call, including reactive end-cursor ranges. Client attempts to raise budgets are overridden. Continuation, end and split cursors carry account/view/state scope and are rejected on mismatches. This wrapper is not cryptographic authentication: each database query independently restricts its index using the signed-in account. Pending invitations use a stable state index and filter expiry after reading a bounded page, so an empty page can still have `isDone: false`.

The old aggregate `friends.list({})` API was removed; all repository callers were migrated. No native/browser friend-list caller or deployed consumer exists in this checkout. Future clients must use the paginated contract and continue until `isDone`, not until `page` is empty. Standard Convex pagination metadata is preserved, including split cursors. The implementation follows the [Convex pagination contract](https://docs.convex.dev/database/pagination) and was checked against installed Convex 1.48.0 sources.

## Actual validation

- **75 backend tests passed across 8 files**, zero failures: previous 64 plus 6 invitation-limit and 5 pagination regressions. Existing friendship API tests migrated and still pass.
- Cases include persisted failure charges; exact refill boundaries; account/operation isolation; simultaneous redemption attempts; quota-independent revocation; multi-page traversal; revoked-history exclusion; wrong-account/view/state cursor reuse; forged wrapper isolation; end/split wrapping; invalid sizes/cursors; and expired pending pages without hash disclosure.
- TypeScript typecheck passed. Offline codegen passed and its generated internal reference replaced the implementation agent's temporary reference. An intermediate test-only inference error was fixed with a schema-derived `FunctionReturnType`; no production type validation was loosened to resolve it.
- Repeated offline codegen produced identical hashes. Tracked/new-source whitespace checks passed. The pre-existing user Xcode scheme hash stayed unchanged; relay/handoff remain ignored and untracked.
- Logs: `/tmp/r2-friend-hardening-tests.log`, `/tmp/r2-friend-hardening-typecheck.log`, `/tmp/r2-friend-hardening-codegen.log`.
- GPT-6 Sol/high implemented the quota subsystem; GPT-6 Astra/high reviewed authorization, persistence and pagination races independently. Parent integrated pagination, migrations, regression tests, generation and documentation. Reviewer inspected source/tests but did not rerun suites.
- Native tests/builds were not rerun because native code was unchanged. Prior verified native checkpoint remains 96 app tests and an unsigned iPhone Release build; this does not establish live-device behavior.

## Remaining work and limits

No deployment, migration analysis, real-account traffic test, production reactive pagination test or measured load test was performed. `convex-test` and read-option assertions do not prove deployed scan-budget enforcement or production concurrency behavior. New indexes/table require normal deployment validation before use against a live database.

`sessions.listJoinable` still scans the helper's friendship history and is a separate pagination follow-up. Account-wide abuse controls, rate limits for costly room provisioning, invitation/history retention and notification delivery remain. Preserve media-room tombstones during future retention work.

ScreenModel/Target Selector/Gemini integration remains reserved for inspecting the teammate's actual unpushed code. Native Auth0/R1 adapters, audio, worker authentication/publisher and helper clients are still unfinished. No existing implementation was deleted, no credentials invented, and no commit, push or deployment occurred.
