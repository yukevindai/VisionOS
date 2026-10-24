# LiveKit backend integration — October 24, 2026

Implemented server-side LiveKit room provisioning, owner/helper tokens, signed webhook ingress, membership reconciliation, and durable media cleanup. **62 local tests passed; no LiveKit/Convex deployment or physical iPhone acceptance was performed.** Native media publication and the worker service are not connected to this backend yet.

## Behavior and authority

The SDK is pinned to `livekit-server-sdk` **2.19.2**. `livekit.provisionRoom`, `issueOwnerToken` and `issueHelperToken` are authenticated Node actions. Mutations prepare durable room/issuance records; actions perform provider I/O/signing; final mutations recheck session, room generation, authorization and exact helper reservation before returning a credential. Signing failures or revocation during asynchronous work do not release a token. A completed issuance cannot be repeated for the same identity; a lost response requires explicit room recovery.

Owner tokens support private sessions with sharing off. Sharing discovery and helper issuance require verified owner membership, accepted friendship, active owner sharing authorization, and a helper reservation. Tokens have short bounded initial expiry, camera/microphone source limits, no administration/recording/listing rights, and no client metadata-authority or data-publishing grant. The SDK helper can sign a worker role, but no worker token API or service ingress exists yet.

`POST /livekit/webhook` verifies the exact raw-body signature using the real SDK and configured signing key, limits bodies to 256 KiB, and deduplicates event IDs. Event metadata never grants authority. Events trigger provider reconciliation using current room and participant SIDs; old leave events cannot release a replacement. Five-second reconciliation and a 30-second action watchdog also handle missed callbacks. Owner disappearance, unissued identities, SID replacement and room mismatch retire the generation.

Any helper-related invalidation retires the whole room name permanently. Cleanup attempts remove recorded identities with an explicit Cloud revocation cutoff as defense in depth, delete the room, then check for absence. Failed or ambiguous cleanup stays pending with exponential retries capped at five minutes; attempt leases/watchdogs recover action crashes. A bounded cron scans up to ten due closed-room tombstones every ten seconds, with a sixty-second audit interval per processed room. Stale provisioning completion/failure requeues cleanup without modifying a newer room generation.

## Isolation limits

An absent-room response is a **provider snapshot**, not proof that cached credentials are unusable. LiveKit tokens can outlive application permissions, receive refreshes, and recreate deleted room names. The backend never reuses retired names; the native owner and worker must also refuse to publish into retired generations. That publisher gate is still unwired, so real privacy/eviction acceptance remains open. Room rotation briefly interrupts private AI media as well; this slice does not prove uninterrupted private AI during helper removal.

The current provider behavior was checked against primary documentation: [LiveKit token lifecycle and revocation](https://docs.livekit.io/frontends/reference/tokens-grants/), [participant removal](https://docs.livekit.io/intro/basics/rooms-participants-tracks/participants/), [signed webhooks](https://docs.livekit.io/intro/basics/rooms-participants-tracks/webhooks-events/), and [Convex action scheduling guarantees](https://docs.convex.dev/scheduling/scheduled-functions). Local mocks do not establish these behaviors on the user's actual deployment.

## Validation

From `backend/` with Node/pnpm on PATH:

```sh
pnpm install --frozen-lockfile
pnpm run codegen
pnpm run typecheck
pnpm test
```

All commands exited 0. **6 files, 62 cases, 0 failures**: auth 4, friendship 4, session policies 22, SDK wrapper 10, provider orchestration 13, race/HTTP ingress 9. The prior 22 session tests were migrated to real issuance/readiness/cleanup boundaries; they were not deleted to accommodate the new implementation.

Tests include real SDK JWT signing/verification with local test keys, mocked SDK HTTP calls, Convex simulated transactions, deferred signer/provisioning races, stale callbacks, signed raw HTTP ingress, duplicate events, cleanup retries/watchdogs, permanent room retirement and old-room recreation audits. No provider network request was made with real credentials.

Security review found and verified fixes for duplicate provisioning after authorization changes, stale-version invalidation after helper confirmation within one snapshot, and late provisioning responses after completed cleanup. Regression tests cover each, including both late success and late failure.

Final logs: `/tmp/r2-livekit-install.log`, `/tmp/r2-livekit-codegen.log`, `/tmp/r2-livekit-typecheck.log`, `/tmp/r2-livekit-tests.log` (final test run 06:01:12 local). Repeat generated-file SHA256 manifests are identical: `/tmp/r2-livekit-generated-before.sha256` and `/tmp/r2-livekit-generated-after.sha256`. Whitespace checks passed.

Environment: Node **24.19.0**, pnpm **11.25.0**, Convex **1.48.0**, convex-test **0.0.65**, TypeScript **7.0.2**, Vitest **5.0.5**, Vite **8.3.5**. The lockfile pins dependencies. Local codegen still uses the pinned CLI's offline `--system-udfs --typecheck disable` bootstrap; normal deployment analysis/codegen remains untested without a configured deployment.

No native Swift/Xcode/R1/H1 module changed. The existing scheme SHA256 remains `540920b42c01d7e87b818a3281cdddf9a71b2ba065ed982bc5972028a7c8f511`. Prior 201 native/package cases and unsigned Release results were not rerun. Historical five strict contrast failures remain unresolved.

## Remaining integration

Configure actual Convex/Auth0/LiveKit server environment values and the actual `.convex.site/livekit/webhook` URL, then run normal deployment codegen and live smoke checks. No account values or secret credentials were invented. Worker tickets/service auth, native/browser transport and publisher-generation enforcement, R1 capture/audio, notifications, task projection/selections, rate limits and historical retention remain unfinished. Preserve permanent room tombstones during retention work. Test cached tokens/reconnects, real eviction, outages and physical iPhone behavior before claiming F13/F26 acceptance.

GPT-6 Sol implemented the SDK wrapper and orchestration tests; GPT-6 Astra reviewed security/race behavior; GPT-6 Luna updated setup documentation. The parent integrated the transactional lifecycle, HTTP ingress, regression tests and local relay. Nothing was committed, pushed or deployed.
