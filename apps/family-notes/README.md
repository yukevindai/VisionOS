# VisionOS Family Notes

A trusted helper leaves a short note for a VisionOS user; the user hears it when they need it,
and the helper sees live when it was heard. Backend: `backend/convex/notes.ts`, `notesAudio.ts`.

## Run

```sh
cd apps/family-notes
pnpm install --frozen-lockfile
cat > .env.local <<'ENV'
VITE_AUTH0_DOMAIN=<Auth0 domain>
VITE_AUTH0_CLIENT_ID=<Auth0 SPA (web) client ID>
VITE_CONVEX_URL=https://<deployment>.convex.cloud
ENV
pnpm dev   # http://localhost:5173 (the origin registered on the Auth0 SPA app)
```

It uses the same Auth0 SPA client and port as `apps/friend-web`, so run one of them at a time.
Open `http://localhost:5173/?view=owner` on the VisionOS user's laptop to start on their notes.

## Connect a helper (existing VisionOS friend trust)

1. Owner: **Notes for me → Invite a helper**, then share the one-time code (valid 24 hours).
2. Helper: **I'm helping someone → Join**, paste the code.
3. Owner: **Approve**. Only approved helpers can send notes; **Remove** revokes them at once.

## How it works

- **Helper tab:** pick a person you help, write a note (or a safety warning) with an optional place,
  send. Your list updates live: *Preparing voice → Voice ready*, then *Heard at 9:02*.
- **Notes for me:** new notes appear live. Turn on **Read new notes aloud automatically** and each
  new note is spoken as it arrives, one at a time. Audio is the Cartesia WAV rendered when the note
  was saved; without it, the browser voice reads the same words. A note counts as *heard* only
  after it played to the end. Search finds notes by words (Convex full-text search, this owner only).

## Convex features used

Live queries (both views), mutations with transactional friendship checks, a search index
filtered by owner, file storage for the rendered audio, and two components:
`@convex-dev/rate-limiter` (per-helper token bucket: burst 5, 20 per hour) and
`@convex-dev/action-retrier` (up to 2 retries of the Cartesia render with exponential backoff and jitter, on timeouts, 429 and 5xx;
bad keys or no credits are not retried).

## Laptop

**Notes for me → Pair this laptop** creates a one-time device key for the VisionOS laptop, which then
speaks new notes between tasks. See [docs/FAMILY_NOTES.md](../../docs/FAMILY_NOTES.md).

## Safety and privacy

- Every note is spoken with its author first ("Note from Lan: …", "Safety note from Lan: …").
- Notes are information, never permission: they cannot pay, press, or declare anything safe.
- Revoking a helper hides their notes and blocks new ones immediately.
- Owner or author can delete a note; its audio is deleted with it.
