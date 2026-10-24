# Reflex backend

This directory contains the Gemini vision service and the Convex live friend assistance backend. Each component has its own setup and validation status below.

## R3 backend: photo → Gemini Vision → ScreenModel → TargetSelection

R3 owns this directory. It takes a photo of a physical control panel or touchscreen plus a user request. It asks Gemini to list the visible interactive elements and converts the reply into the Step 1 `ScreenModel` (`ios/Packages/Targets`). It then runs the target selector, returning `SELECTED`, `NOT_FOUND` or `AMBIGUOUS`.

Python 3.10+, standard library plus Pillow. Gemini is called over REST; there is no SDK dependency.

### Status (2026-10-24)

- **Offline Python tests:** 34/34 pass after native bridge integration. Provider replies are **SIMULATED**; HTTP tests use localhost only. The Convex suite passes 100 tests across 11 files, including eight authenticated vision bridge tests.
- **Live Gemini provider check (October 24):** Gemini 3.8 Flash with `GEMINI_THINKING_LEVEL=low` returned HTTP 200 and selected button “2” from a **synthetic** two-button panel in 2.34 seconds. This is one real API request, not a real scene or deployed iPhone test. The default-effort request exceeded the five-second limit; 2.5 Flash returned 404 despite appearing in the model list. See [native integration validation](../ios/Validation/2026-10-24-native-r1-r3.md).
- **OCR:** not added. No OCR engine is installed here, so Gemini does the reading. Add OCR only if live evaluation shows label errors.

### Setup and run

```sh
cd backend
python -m pip install -r requirements.txt
cp .env.example .env          # then set GEMINI_API_KEY in .env (ignored by Git)
python -m unittest discover -s tests
python -m r3_vision.cli path/to/photo.jpg "Find floor 5"
python -m r3_vision.server    # private http://127.0.0.1:8787/v1/read-screen
```

The HTTP service is private and requires `Authorization: Bearer <R3_VISION_SERVICE_TOKEN>`. The old unauthenticated `/v1/select` route was removed. CLI/evaluation still use the portable Python selector; native reads use the Swift `Targets.TargetSelector` reference implementation.

### Native app → Convex → Python → Gemini

`vision:readScreen({imageBase64, frameId, capturedAtMs, requestId})` is the public authenticated Convex action. It requires an existing signed-in account (`users:ensure` first), independently of friend sharing or media readiness. It returns `{requestId, screenModel}`. Native code checks correlation and interprets semantic evidence through the teammate Targets package; the result never authorizes physical movement.

1. On the Python host, set `GEMINI_API_KEY`, an explicitly chosen `GEMINI_MODEL`, and a random server-only `R3_VISION_SERVICE_TOKEN` of at least 32 printable ASCII characters. Install `requirements.txt` and run `python -m r3_vision.server`.
2. Put an HTTPS reverse proxy in front of its loopback port (default `8787`). Forward only the private `POST /v1/read-screen` route. The service checks the bearer token before reading images or calling Gemini. Actual hosting/domain values must come from the selected deployment.
3. In Convex server environment settings, set `R3_VISION_URL` to that exact HTTPS endpoint (including `/v1/read-screen`) and the matching `R3_VISION_SERVICE_TOKEN`. Neither value is an iOS/browser secret setting; the token must remain server-side. Gemini credentials remain on the Python host.
4. Deploy the Convex functions to the configured development project. No deployment or provider credentials were created by the local integration work.

The proxy rejects redirects and non-HTTPS endpoints. Requests carry at most 512 KiB of decoded JPEG/PNG data, identifiers up to 128 characters, and nonnegative safe-integer UTC capture time. The Python image decoder accepts JPEG/PNG up to 16 megapixels. Responses are limited to 256 KiB; request/frame/timestamp mismatches are rejected. Gemini timeout is capped at 5 seconds and the proxy at 6 seconds. Images are passed through transiently, not stored in Convex or logs. Missing service config, timeout, HTTP failures and malformed responses are explicit errors; a provider-unavailable ScreenModel never means the requested target was absent.

Offline bridge validation covers unauthenticated/unregistered callers, private reads without media, credential isolation, payload/response bounds, correlation, HTTP auth, timeout and malformed Gemini JSON. These tests use simulated providers; they do not establish real Gemini accuracy, cloud availability or device behavior.

The CLI/evaluation result has five parts:
- `mode`: `REAL_API` or `SIMULATED`
- `screenModel`
- `selection`
- `provider`: `ok`, `httpStatus`, the verbatim `error`, and token `usage`
- `timingMs`: `prepare`, `provider`, `select`, `total`

### Evaluation with real photos

1. Put photos in `eval/photos/` (ignored by Git). Use at least two elevator panels and two kiosks, from different buildings and brands.
2. Copy `eval/manifest.example.json` to `eval/manifest.json`. For each photo, list requests with the expected `status`. For `SELECTED`, add the expected `label` and a **manually reviewed** `box` (normalized x, y, width, height in the upright photo). Include at least one missing target and one ambiguous target per interface type.
3. Run `python eval/evaluate.py eval/manifest.json`. The summary reports:
   - recognition accuracy
   - box IoU (mean, and count ≥ 0.5)
   - missing/ambiguous handling (exact status, and "safe" meaning never `SELECTED`)
   - provider and total latency p50/p95
   - every API error verbatim
4. Review the overlays in `eval/results/<run>/`: Gemini's boxes in red, ground truth in green. Results are labelled `REAL_API`. `--replay-dir` runs produce `SIMULATED` results and never count as live.

### Cartesia speech (Step 5)

`speech:synthesize({ requestId, text })` is a signed-in Convex action. The iOS app calls it only for priority-4 `.speak` lines; priorities 0–3 (hand guidance, Stop, safety) stay on device. Sign in before guiding: the app never opens a login prompt mid-task and uses local speech when signed out.

Deploy (dev):

1. Set the Auth0 values from [the setup guide](../docs/LIVE_VIDEO_DEMO_SETUP.md) plus `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID` and `CARTESIA_MODEL` (see `.env.example`) on the deployment with `pnpm exec convex env set`. LiveKit values are not needed for speech.
2. `pnpm run typecheck`, then `pnpm exec convex dev --once --typecheck=disable`. The repo typechecks with `backend/tsconfig.json`; delete a CLI-generated `convex/tsconfig.json` if one appears, and do not commit CLI changes to `convex/_generated`.
3. Offline provider check: `node --env-file=.env scripts/cartesiaSmoke.ts "Say hello"` writes `eval/results/cartesia-smoke.wav`.

Behaviour the app relies on:

- Missing or partial credentials return `{ ok: false, errorCode: "SPEECH_NOT_CONFIGURED" }`, never silent audio, so the app falls back to on-device speech.
- Text is trimmed and capped at 300 characters; the provider call times out after 5 s; responses must be WAV (24 kHz PCM16) under 4 MB.
- Successful audio is cached in Convex file storage keyed by sha256(model, voice, text). The table stores the digest, not the transcript. Failures are never cached. Changing the voice or model misses the cache.
- Each uncached request costs one Cartesia credit per character. Errors never include the API key.
- Modules that import Node built-ins (`lib/cartesia.ts`, `lib/visionService.ts`, `speech.ts`) start with `"use node"`; without it `convex dev` fails with "Could not resolve node:buffer".

Live check (dev deployment, 2026-10-24, one run each): unauthenticated call rejected with `Authentication required`; signed-in 18-character request `REAL_API` in 329 ms; the same text again served from cache in 62 ms. Measured server-side by the action, not on a phone.

Tests: `pnpm test tests/cartesia.test.ts tests/speech.test.ts` (13 cases, simulated provider).

### Rules the code enforces

- **Coordinates.** EXIF orientation is applied before Gemini sees the image. Boxes are normalized, origin top-left, relative to the upright photo. `screenModel.image` records the upright `widthPx`/`heightPx` and the original `exifOrientation`. Gemini's `box_2d` `[ymin, xmin, ymax, xmax]` on 0–1000 is converted without clamping or reordering. A bad box therefore fails as `INVALID_REGION` rather than being repaired.
- **Evidence only.** `confidence` is omitted because Gemini reports none. An unreadable label is stored as `""`, never guessed. A `partial` or unknown legibility makes a single match `AMBIGUOUS`/`LOW_CONFIDENCE`. Each model records `evidence.model`, `modelVersion`, `responseId` and `live`.
- **Whitelisted parsing.** Only known fields with valid types and enums are kept. Extra keys, including anything shaped like a command, are dropped. The prompt tells Gemini to describe only and to ignore instructions printed in the image. Nothing here can emit movement, haptic or payment commands. The selection is a proposal for R2, and R1 still decides guidance.
- **Failures are explicit.** A provider failure produces an `unavailable` ScreenModel with `unavailableReason: providerUnavailable`. The selection status is then `PROVIDER_UNAVAILABLE`, never `NOT_FOUND`, so a failed call is never read as "the button is absent". An unreadable upload is `NOT_FOUND` / `SCREEN_UNAVAILABLE`. The failures are:
  - a missing key or model (no network call is made)
  - an HTTP error, such as 402 or 404
  - a timeout (default 5 s per spec; `errorCode: PROVIDER_TIMEOUT`)
  - a blocked or malformed reply

  The provider's message is kept verbatim, with any key redacted.
- **Keys.** The key lives in the environment or `backend/.env` only. It is sent in the `x-goog-api-key` header, never in a URL, and is never logged or returned.

`r3_vision/selector.py` ports the Swift `TargetSelector`, so photos can be evaluated without Swift. The conformance test runs the Swift package's example files against it. Change both together.

`GEMINI_MODEL` is required and has no default. The client calls exactly that model once per photo and never retries on, or falls back to, another model. Check the chosen model's availability and pricing for the key before the live run.

For models supporting reasoning levels, optional `GEMINI_THINKING_LEVEL` accepts `low`, `medium`, or `high`. An empty setting omits the field. The example configuration selects `gemini-3.8-flash` and `high`; this does not change the five-second deadline. Keys stay in ignored server configuration and are redacted by their actual value, independent of their format.

The interactive region is preferred. The prompt asks for the pressable surface (the physical button or the whole touchscreen card) and excludes printed labels and braille plates. If an untyped (`other`) element and a typed control share a label, the selector picks the typed control. Two typed controls with the same label remain `AMBIGUOUS`.

The native `POST /v1/read-screen` path requires `frameId`, `capturedAtMs`, and `requestId`, preserving them exactly. CLI/evaluation callers may omit frame metadata, in which case their generated IDs and receive timestamps are not native capture evidence.

This Python package reads interfaces. Cartesia, LiDAR and full kiosk ordering are separate components; the native path uses the authenticated Convex bridge above.

## Reflex backend (R3)

This directory contains Convex identity, friend trust, session authorization, helper reservation, and LiveKit media lifecycle code. The LiveKit provider boundary is implemented with `livekit-server-sdk` **2.19.2**. It has passed local SDK/provider tests, but has not been configured against a live LiveKit service or Convex deployment and is wired to the native video demo and browser helper, pending real service setup. Do not describe it as live-call validated.

### Local setup

Use Node.js **24.19** and pnpm **11.25** (the versions used for this slice). From this directory:

```sh
pnpm install --frozen-lockfile
pnpm run codegen
pnpm run typecheck
pnpm test
```

Local generation, typechecking and tests need no service credentials. For a real deployment, use `.env.example` as the configuration inventory and set values in the Convex deployment environment through its dashboard or authenticated CLI. Copying a local file does not configure cloud environment variables. Use the exact HTTPS JWT issuer URL and native/web client IDs from the actual Auth0 applications, including the issuer's trailing slash when present. Configure native/browser login and redirect URLs separately. Keep deployment configuration and secrets untracked.

`pnpm run codegen` uses the pinned Convex CLI's `--system-udfs` option to generate standard app typings locally without cloud deployment configuration; `--typecheck disable` avoids requiring deployment-side typecheck setup during this local generation. This is an offline typing step, not deployment validation. Once connected to a configured Convex deployment, use `pnpm run codegen:deployment` for normal deployment code generation. `pnpm run dev` starts the Convex development workflow and requires a configured deployment and valid auth settings.

Pinned direct dependencies include Convex **1.48.0**, `livekit-server-sdk` **2.19.2**, `convex-test` **0.0.65**, TypeScript **7.0.2**, Vitest **5.0.5**, and Vite **8.3.5**; `pnpm-lock.yaml` pins the full dependency graph.

### Implemented surface

Public client functions authenticate through Convex identity and map `(issuer, subject)` to a user record. `users.ensure`, `users.me`, `users.setAutomaticSharing`, and `users.setDisplayName` manage that account. Sharing defaults off.

Friend trust is directional. Public `invites.createInvite` and `invites.redeemInvite` actions create high-entropy single-use codes and persist only their SHA-256 hashes. `friends.list`, `friends.approveInvite`, `friends.revoke`, and `friends.setNotifications` expose approved friend relationships and owner approval. Only internal mutations store or consume invite hashes. Unbound codes require explicit owner approval of the redeeming account; bound codes can be redeemed only by that account. Codes last 5 minutes to 7 days, with at most 16 outstanding invitations per owner. Notification preferences default off and must be enabled by the accepted helper; push delivery is not implemented.

Invitation attempts are charged in a separate committed internal mutation before format/business validation. `createInvite` permits a burst of 5 and refills one attempt per 60 seconds; `redeemInvite` permits 10 and refills one per 10 seconds. Failure still consumes quota. A denial throws `ConvexError` with data `{ code: "RATE_LIMITED", retryAfterMs }`. These initial policies live in `convex/inviteLimits.ts`; one budget row per verified account/operation bounds storage. Convex argument-envelope failures may occur before charging. Revocation and access-closing operations are not throttled by invitation budgets.

Friend lists now use paginated queries (the previous `friends.list({})` aggregate was removed):

```ts
await client.query(api.friends.list, {
  direction: "owned", // or "helping"
  state: "accepted", // or "pending"
  paginationOpts: { numItems: 20, cursor: null },
});
await client.query(api.friends.pendingInvites, {
  paginationOpts: { numItems: 20, cursor: null },
});
```

Use `continueCursor` for the next page until `isDone`. Pending approvals can produce an empty intermediate page after expired rows are filtered. List rows contain `friendship` and `counterpart` (display name/ID only); pending approval rows contain invite ID, expiry and requester display identity, never a code/hash. Requested page sizes must be 1–50. Server scan budgets override client overrides. Cursors are specific to authenticated account, direction and state; reset them when any changes. Standard reactive split/end-cursor metadata is preserved. New `by_owner_state`/`by_helper_state` friendship indexes exclude revoked history at query time. Session discovery uses the same bounded pagination conventions:

```ts
await client.query(api.sessions.listJoinable, {
  paginationOpts: { numItems: 20, cursor: null },
});
```

The result contains `page` summaries `{ sessionId, epoch, ownerDisplayName }` and standard continuation/split metadata. It pages accepted helper friendships, rechecks current session/sharing leases and media readiness/removal for each candidate, and bounds each owner lookup to two active sessions. Duplicate active sessions fail closed for that owner. An empty page can be nonterminal; continue until `isDone`. Discovery does not reserve a helper slot or authorize media, and the old `listJoinable({})` array contract is removed.

For live-session bookkeeping, public `sessions.start/get/mediaStatus/heartbeat/setSharing/end/listJoinable` and `assistance.reserveHelper/helperStatus/heartbeat/renewAuthorization/leave/removeHelper/restoreHelper` enforce signed-in owner/helper identity, accepted friendship, session epoch, one helper slot, lease version, authorization version, and expiry. `assistance.renewAuthorization` is owner-authenticated; its nonce is an opaque correlation value scoped to that renewal response, not a reusable credential. Internal `workerAuthorization`, media state functions, and scheduled expiry handlers are not public client APIs or webhook endpoints. Native `sessions.mediaStatus` returns an owner-only nonce-correlated projection with the exact room generation/SID, verified helper participant SID, and at most 15 seconds of remaining owner presence. It does not renew sharing. Native clients anchor durations to the local monotonic request-send time and obtain sharing authority separately.

The owner sends presence heartbeats every 5 seconds (20-second expiry) and separately renews sharing authorization every 5 seconds (at most 15 seconds). A ready room is not discoverable until the owner has connected and reconciliation confirms that owner participant; sharing must also have a current owner grant. Helper reservations last at most 15 seconds, capped by owner presence and sharing deadlines. Helper heartbeats and verified connection events never extend owner authorization. Token issuance prepares and then finalizes a permit against current session/epoch, room, grant, role identity, and (for helpers) the exact reservation and client instance. Only one token issuance is allowed per role identity in a room. If a token response is lost after issuance, that role identity cannot simply request another token: the owner must explicitly call `mediaState.recover`, wait for verified cleanup, then provision a fresh room. The native HTTP adapter maps server IDs to its local session binding; clients must preserve the exact epoch and room generation.

The public LiveKit Node actions are:

- `livekit.provisionRoom({ sessionId, epoch })` provisions a private room for the authenticated owner session. Provisioning is session-bound and never rebinds an existing room name.
- `livekit.issueOwnerToken({ sessionId, epoch, clientInstanceId })` issues the owner role token after server-side authorization and room checks.
- `livekit.issueHelperToken({ sessionId, epoch, clientInstanceId })` issues a helper token only for that authenticated helper's current reservation.

Tokens are signed server-side with bounded expiry and minimal room grants. The SDK signer can represent an internal `worker` role, but there is no worker token API, ingress, ticket minting or ticket validation. Do not expose provider credentials or internal mutations to clients.

The signed LiveKit HTTP webhook is `POST /livekit/webhook`, served at the actual Convex `.site` deployment origin, for example `https://<actual-deployment>.convex.site/livekit/webhook`. Configure the LiveKit project's webhook to that exact HTTPS endpoint after obtaining the real deployment URL. The handler preserves the raw body, verifies the LiveKit signature, bounds request size, deduplicates event IDs, and treats event payloads as reconciliation triggers; webhook content does not directly grant or release a lease. No actual deployment URL is known or assumed here.

Room reconciliation is scheduled every 5 seconds for active rooms after initial reconciliation, with a 30-second crash watchdog for an in-flight reconcile action. A bounded cron audit scans up to 10 closed room tombstones every 10 seconds when their 60-second audit deadline is due. Cleanup actions are crash-watched and retry with exponential bounded backoff up to 5 minutes. Cleanup deletes the room and confirms an absent-room provider snapshot before clearing the cleanup blocker; this is a verified snapshot at that time, not proof that cached credentials were revoked. Old LiveKit tokens can recreate an old room, so room names are permanent tombstones and must never be reused. Cloud explicit revocation cutoffs are defense in depth, not an isolation proof.

The native owner publisher generation gate is wired into the standalone video demo; the worker publisher remains unimplemented. A participant holding an old token could recreate the old room; safety therefore depends on never reusing that name and on native publishers refusing stale generations. Helper loss/removal retires and rotates the room to contain stale credentials. That room rotation briefly disconnects private AI media too; private AI remains supported, but uninterrupted AI during rotation is not guaranteed. Provider calls and cleanup logic are implemented, but their real service behavior remains live-unverified.

Public media actions have independent per-caller budgets: room provisioning burst 3/refill one per 30 seconds, owner-token and helper-token each burst 6/refill one per 10 seconds. A separate committed internal mutation charges before configuration/provider/signing/state work; failed attempts and retries count. Exhaustion returns `ConvexError` data `{ code: "RATE_LIMITED", retryAfterMs }`. Budgets use the verified caller account and server-selected operation, so rotating sessions/epochs/client IDs cannot reset them and helper attempts cannot consume an owner's budget. Policies live in `convex/mediaRequestLimits.ts`; they are initial settings, not measured provider capacity. End, Stop Sharing, leave/remove/revoke, recovery, signed webhooks, reconciliation and cleanup remain outside these budgets. Native media currently closes on action rejection; retry UI/backoff is future integration work.

The durable `mediaCleanup` record is an outbox intent. `media.finishCleanup` is not a client self-attestation API; only the trusted provider path may complete a claimed cleanup attempt after its provider snapshot. Revocation, leave, removal, expiry, sharing-off with a helper, and explicit recovery invalidate authorization and enqueue cleanup. Replacement joins wait for verified cleanup and a fresh owner grant. Session removals persist until explicit owner restore, which does not restore revoked friendship. Changing automatic-sharing preference affects future Starts; `sessions.setSharing` controls the current session.

### Current limits and validation

Tests use `convex-test`, simulated identities/state, and mocked provider behavior. They do not validate live Auth0 JWT issuance, Convex deployment/auth configuration, a live LiveKit room, webhook delivery, actual media eviction, native publisher generation enforcement, notifications, or physical/native integration. Do not count a simulation pass as a live-device or live-service pass. Required Auth0 and LiveKit values are placeholders in `.env.example`; never paste secrets into chat or commit them.

The discovery/media-limit suite passed **90 tests across 10 files**, plus TypeScript typechecking and reproducible offline code generation. Dependencies and lockfile were unchanged; frozen installation was last verified in the earlier LiveKit checkpoint. See the [discovery/media-limit validation record](../ios/Validation/2026-10-24-discovery-media-limits.md), the [friend hardening validation record](../ios/Validation/2026-10-24-friend-backend-hardening.md), the [native media validation record](../ios/Validation/2026-10-24-native-media-transport.md) and the earlier [LiveKit validation record](../ios/Validation/2026-10-24-livekit-media-integration.md); the [earlier foundation record](../ios/Validation/2026-10-24-friend-convex-foundation.md) preserves the preceding 30-case checkpoint. History retention/garbage collection, production quota tuning and broader abuse controls remain follow-up work; active session lookups use an owner/status index so they do not scan all historical sessions. Preserve permanent room-name tombstones when adding retention.

Worker service-role ingress/tickets and native/browser helper client hookup remain unimplemented. The optional native video adapter validates renewal nonce/scope; actual native Auth0 and R1 capture/audio adapters still need integration. Actual LiveKit/Convex deployment setup and live acceptance are also outstanding.

Primary references: [Convex Auth0 integration](https://docs.convex.dev/auth/auth0), [Convex testing](https://docs.convex.dev/testing/convex-test), and [LiveKit server SDK](https://github.com/livekit/node-sdks/tree/main/packages/livekit-server-sdk).

### Real video demo integration

Follow [the end-to-end setup guide](../docs/LIVE_VIDEO_DEMO_SETUP.md) to create the missing Convex/Auth0/LiveKit projects, set server secrets and public client configuration, then test on iPhone. The browser viewer lives in `apps/friend-web`; the native entry is **Share live video with a friend**. No Gemini key is needed.

`assistance.helperStatus({sessionId,epoch,connectionId,leaseVersion,nonce})` returns `closed`, `reserved`, or `connected` only for the authenticated helper's current accepted and unexpired slot. Open states include current room name/SID/generation, authorization version, provider-confirmed owner identity/SID and at most 15 seconds of remaining duration; connected also includes the helper participant SID. Clients anchor this to request-send monotonic time. It neither renews nor grants a lease. The viewer subscribes to the exact verified owner camera only after connected status matches its own provider SID.

Current backend suite: **92 tests across 10 files**, plus typecheck and offline codegen passed. The two added status-query tests cover reserved/connected identity scope, expiry/revocation and no lease renewal. This is still local simulated-service validation. See [the video-demo validation record](../ios/Validation/2026-10-24-live-video-demo.md).

## Windows live camera experiment

See [LIVE_CAMERA.md](LIVE_CAMERA.md) for PathOn connection instructions, on-demand capture,
frozen target overlays, desktop ROI fallback and recorded transform metadata. Start from the
outer workspace with `powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-camera.ps1`.
This desktop experiment has no movement/audio/haptic outputs and does not replace native R1.

## Continuous live 2D tracking

[Windows tracking instructions](LIVE_TRACKING.md) describe the new **Find & Track** tool:
`powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1`.
It matches Gemini's historical reference to current camera frames before drawing a live box,
hides lost/stale targets, and bounds automatic re-identification requests. The frozen camera
program remains available unchanged. This is a visual test, not native R1 or movement guidance.
