# Convex auth, friend trust and session leases — October 24, 2026

The backend foundation is implemented under `backend/`. **30 local tests passed; no live service or physical-device acceptance was performed.** The existing native coordinator is unchanged in this slice and does not yet call these APIs.

## Implemented behavior

- Auth0 verifier configuration requires actual HTTPS issuer and native/web client audiences, without default production values. Public functions resolve Convex-verified identity by `(issuer, subject)`; caller-supplied user IDs or roles do not establish authority. Local tests inject identities and separately check config validation; they do not verify real JWT signatures.
- Owner-generated, SHA-256-hashed, single-use invitations use Node cryptographic randomness. An unbound invite requires owner approval; an account-bound invite checks the exact account. Friendship is directional, notifications default off, and revocation also invalidates pending/bound invitations before redemption.
- Authenticated private sessions work with sharing off. Start retries reuse the same session identity without revival; a fresh epoch ends the old session and requires a distinct room. Presence expires after 20 seconds. Public discovery requires accepted trust, internally verified media readiness, sharing permission, an unexpired owner sharing grant, and no removal/cleanup/occupied-slot blocker.
- One transactional helper reservation lasts at most 15 seconds and is capped by owner presence and sharing authorization. Only an internal provider-adapter boundary can confirm the exact room/connection/lease. A second device cannot replace an occupied slot. Duplicate verified confirmation is idempotent.
- Owner authorization renewal echoes the native-compatible nonce, exact session/epoch/version/helper scope and a duration of at most 15 seconds. Worker grants cannot extend owner authorization. Helper heartbeats, owner presence alone and connection callbacks cannot renew sharing permission. The native transport must still map Convex IDs to native UUIDs and preserve request-send-time deadline calculation.
- End, revocation, removal, leave and expiry atomically invalidate access and persist media-cleanup intent. Old leave/expiry/cleanup callbacks cannot clear a replacement. New grants wait for verified cleanup and fresh owner authority; an HTTP eviction response alone is not sufficient evidence. This slice implements neither delivery nor actual eviction.
- Scheduled cleanup callbacks are tested with the Convex test scheduler. Active session reads use an owner/status index; history retention and friendship pagination remain future work.

## Validation

Environment: bundled Node **24.19.0**, pnpm **11.25.0**. Pinned direct packages: Convex **1.48.0**, convex-test **0.0.65**, TypeScript **7.0.2**, Vitest **5.0.5**, Vite **8.3.5**, edge-runtime/vm **5.0.0**, Node types **26.6.5**. The lockfile pins transitive dependencies. esbuild installation is explicitly allowed in the pnpm workspace configuration.

Run from `backend/` with Node/pnpm on PATH:

```sh
pnpm install --frozen-lockfile
pnpm run codegen
pnpm run typecheck
pnpm test
```

All four commands exited 0. Final tests: **3 files, 30 cases, 0 failures**: auth configuration/account mapping 4, friendships 4, sessions/authorization/cleanup races 22. Coverage includes unrelated users, extra forged role fields, wrong epochs/scopes, invite reuse/expiry/pre-redemption revocation, owner approval, concurrent reservations, reservation expiry, owner-only renewal, exact expiry boundaries, simultaneous expiry callbacks in both orders, late connection, and stale leave/cleanup.

Logs on the development machine: `/tmp/r2-convex-install.log`, `/tmp/r2-convex-codegen.log`, `/tmp/r2-convex-typecheck.log`, `/tmp/r2-convex-tests.log`. Repeat code generation produced identical hashes in `/tmp/r2-convex-generated-before.sha256` and `/tmp/r2-convex-generated-after.sha256`. `git diff --check` passed.

Normal Convex codegen requires a configured deployment in this SDK. The local `codegen` command uses the pinned CLI's internal `--system-udfs --typecheck disable` path to generate standard schema-derived application declarations offline. This does not analyze/deploy the application on Convex. Use `codegen:deployment` after configuring a real deployment; confirm compatibility there before live acceptance. No deployment name, issuer, audience, API key or real room was fabricated.

All new tests use `convex-test`'s simulated runtime; it allows test calls to internal functions. Internal API exposure and real cloud transaction/scheduler behavior still require service acceptance. Simulated identities and rooms are test fixtures only.

## Preserved state and next boundary

No native Swift implementation, Xcode project/scheme, R1 or H1 module changed in this slice. The pre-existing scheme SHA256 remains `540920b42c01d7e87b818a3281cdddf9a71b2ba065ed982bc5972028a7c8f511`. Prior native/package results remain historical: 201 cases and unsigned Release passed; unchanged native tests were not rerun. Historical five strict contrast failures remain unresolved.

The next slice is service ingress: actual Auth0/Convex configuration, scoped media and worker tickets, private-room provisioning, signed/deduplicated/reconciled LiveKit callbacks, cleanup delivery and verified token/participant isolation. Then implement the native/browser transports and verify service/device behavior. Worker tickets, helper Pause/task projection, selections, notification delivery, retention/pagination, live media eviction, R1 integration and physical iPhone acceptance remain unfinished. Nothing was committed, pushed or deployed.

Mixed-model delegation: GPT-6 Sol implemented auth/trust, GPT-6 Astra reviewed authorization/race boundaries, GPT-6 Luna prepared backend documentation; the parent integrated session/lease logic, tests and the local relay.
