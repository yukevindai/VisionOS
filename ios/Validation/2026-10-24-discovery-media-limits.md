# Session discovery pagination and media request limits — October 24, 2026

Implemented the requested backend slice without changing the teammate's ScreenModel, Target Selector or Gemini pipeline. Native Swift/media, R1 capture/audio and H1 hardware implementations were unchanged. No API keys were needed or accessed; tests use simulated identities/provider behavior.

## Session discovery

`sessions.listJoinable` now requires `{ paginationOpts: { numItems, cursor } }` and returns standard pagination metadata plus `page` entries containing only `sessionId`, `epoch` and `ownerDisplayName`. The previous unbounded `listJoinable({})` array API was removed, and all repository callers were migrated.

The query pages the signed-in helper's **accepted** friendships using the existing helper/state index. It reuses the server-owned 1–50 requested page size, 50-row/64-KiB friendship read budgets, 16-KiB cursor limit and account/view-scoped continuation/end/split cursor handling. The discovery scope is distinct from friend-list and pending-invitation scopes. Cursor wrappers are not cryptographic authorization; every underlying query independently filters by the authenticated account.

For each friendship, at most two active owner sessions are read. Exactly one must exist; absent or duplicate active sessions are omitted. This bounds fan-out even if malformed legacy data violates the ordinary single-active-session invariant. It then checks owner presence, sharing permission and lease, ready room, reconciled owner connectivity, cleanup state, empty helper slot, and per-session removal. Self-owned malformed grants and missing owner accounts are omitted. No room credentials or participant details are exposed.

Filtering happens after the friendship page read. A page may be empty with `isDone: false`; clients must continue using `continueCursor` until `isDone`, preserving split metadata for reactive clients. Discovery is advisory and does not reserve a slot, renew a lease or grant media access. The existing reservation/token paths still recheck current trust and session authority.

## Media request limits

A separate committed internal mutation charges a durable budget before public media actions read provider configuration, create a provider client, mutate room/token intent, access LiveKit or sign a token. Failed authorization, provider errors, signing errors and retries still consume admitted attempts. Denied calls return `ConvexError` data `{ code: "RATE_LIMITED", retryAfterMs }` without reaching those operations.

| Public action | Burst | Refill |
|---|---:|---|
| `livekit.provisionRoom` | 3 | One attempt per 30 seconds |
| `livekit.issueOwnerToken` | 6 | One attempt per 10 seconds |
| `livekit.issueHelperToken` | 6 | One attempt per 10 seconds |

Policies are initial application settings in `convex/mediaRequestLimits.ts`, not measured production capacity. The arrival-time limiter stores at most one `mediaRequestBudgets` row per verified caller and server-selected operation. Session IDs, epochs and client instance IDs cannot reset it. Helper calls charge the helper's account, never the target owner's. Accounts must already exist through authenticated setup; anonymous/unregistered callers do not create budget rows. Invalid Convex argument envelopes may fail before action execution and are not charged.

Existing pre/post-provider authority checks, token permits, room generations, reconciliation and cleanup semantics are preserved. End, Stop Sharing, leave/remove/revoke, explicit recovery, signed webhook handling, reconciliation and cleanup are outside these public-action budgets. End/revoke and cleanup/reconciliation are regression-tested after exhaustion. No access-closing operation needs quota to run.

Clients should honor `retryAfterMs` without automatic reconnect or movement resume. The existing native transport remains fail-closed on action rejection; this slice does not add native retry UI or automatic backoff. Session turnover does not reset quota, so repeated explicit retries can remain limited.

## Actual validation

- **90 backend tests passed across 10 files**, zero failures: retained 75 plus 8 discovery and 7 media-limit regressions.
- Discovery cases cover bounded traversal, revoked/pending-history exclusion, exact summary projection, empty intermediate pages, trust/removal changes between pages, lease expiry at equality, all readiness/occupancy gates, duplicate-owner/self-grant rejection, unauthorized/cross-account/view/end cursors and forged-wrapper isolation.
- Media cases cover durable failed charges, exact refill boundaries, session/epoch/client-ID rotation, caller/role isolation, simultaneous attempts, denial before provider/config/permit/signing work, and End/revocation/reconcile/cleanup after exhaustion. Existing delayed-token/room and revocation race tests still pass.
- `pnpm run typecheck` and offline `pnpm run codegen` passed. Logs: `/tmp/r2-discovery-media-tests.log`, `/tmp/r2-discovery-media-typecheck.log`, `/tmp/r2-discovery-media-codegen.log`.
- Repeated offline codegen produced identical hashes (`/tmp/r2-discovery-media-codegen-repeat.log`). Tracked/new-source whitespace checks passed, local relay/handoff remain ignored/untracked, and the pre-existing Xcode scheme hash is unchanged.
- GPT-6 Sol/high implemented media quotas; GPT-6 Astra/high independently reviewed discovery and limits; parent implemented discovery, caller migration, regression tests, integration and documentation. Read-only reviewer found no material issue but did not rerun tests.
- No native tests/builds were rerun because native sources were unchanged. Prior native report retains 96 app tests and unsigned iPhone Release success; neither establishes live-device acceptance.

## Remaining work and credentials

No deployment, schema migration analysis, real JWT acceptance, provider quota/load measurement or production reactive pagination test was run. `convex-test` and configured scan budgets do not prove live database budget enforcement/concurrency. New `mediaRequestBudgets` schema needs ordinary deployment validation. These per-account limits do not solve multi-account abuse, service-wide concurrency or account creation abuse.

Before live integration testing, authorized Convex deployment configuration, Auth0 issuer/client IDs and LiveKit URL/API key/API secret are required. LiveKit credentials belong in Convex server environment settings, never native/browser clients or chat. Required variable names and non-secret placeholders remain in `backend/.env.example`. No Gemini key is required for this slice; teammate vision work remains untouched and unverified until their source/live evidence is available.

Native identity/R1 frame and audio adapters, worker authentication/tickets/publisher, helper UI and notifications remain. Cached/refreshed media tokens and SDK reconnect buffering still require live-device fault testing; room deletion does not prove token revocation. No commit, push or deployment occurred, and local relay/handoff stay ignored.
