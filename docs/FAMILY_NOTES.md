# Family notes

Trusted helpers (family) leave short notes for a VisionOS user. The user hears them when they
need them, between tasks, and the helper sees live when each note was heard.

> Lan can't always be on a call with her dad. She writes once: "The white bottle is your new
> blood pressure medicine." His VisionOS laptop says it between tasks, and her phone shows
> "Heard at 2:41".

## Pieces

| Piece | Where | Owner |
|---|---|---|
| Notes, search, review dates, receipts | `backend/convex/notes.ts`, `noteTables.ts` | R3 |
| Device pairing (laptop key, hash only) | `backend/convex/devices.ts`, `devicesNode.ts` | R3 |
| Cartesia note audio (rendered once) | `backend/convex/notesAudio.ts` | R3 |
| Asymmetric object matching | `backend/convex/lib/familyMatch.ts` | R3 |
| Web page for helpers and the user | `apps/family-notes/` | R3 |
| Laptop listener (Convex Python subscription) | `backend/r3_vision/family_notes.py` | R3 |
| Hook into the laptop pipeline | `backend/r3_vision/live_tracking.py` | R3 (review: laptop owner) |

## Convex features

Live queries (web views and the laptop's Python subscription), transactional friendship
checks (revoking a helper hides their notes in the same query), a search index filtered by
owner, file storage for audio, the scheduler (review dates), and two components:
`@convex-dev/rate-limiter` (per helper: burst 5, 20 per hour) and `@convex-dev/action-retrier`
(up to 2 retries of the Cartesia render with exponential backoff and jitter, on timeouts, 429 and 5xx only; never on bad keys or exhausted credits).

## Safety rules the code enforces

- Every note is spoken with its author first: "Note from Lan: …", "Safety note from Lan: …".
- Notes are information, never permission: nothing starts guidance, confirms a press or pays.
- The laptop speaks a note only while the pipeline is idle and nothing else is playing. The
  one exception: notes about the object a find task just locked onto play once, right
  before hand-guidance audio (bounded wait, haptics continue). Stop and new requests always win; an interrupted note is not marked heard and is
  spoken again later. "Heard" is recorded only after the note played to the end.
- Object matching is asymmetric. A warning is read if any meaningful label word appears in
  it; reassurance needs every word and the same place, and is spoken as "According to Lan's
  note", never as fact.
- After its review date a note is read as "N days old, may be out of date" until the helper
  confirms it ("Still correct").
- Only helpers the user approved (existing invites) can write. Removing a helper revokes all
  of their VisionOS access, including video help. Removing a device stops it at once.

## Laptop setup

1. Deploy the backend (`pnpm exec convex dev --once --typecheck=disable` in `backend/`).
2. On the Family Notes page as the user: **Notes for me → Pair this laptop**, copy the line.
3. In the laptop's `backend/.env`: `CONVEX_URL=https://<deployment>.convex.cloud` and the
   copied `FAMILY_NOTES_DEVICE_KEY=…`. Then `pip install -r requirements-family-notes.txt`.

## Laptop pipeline hook (live_tracking.py)

`TrackingWorker` turns Family Notes on only when `CONVEX_URL` and `FAMILY_NOTES_DEVICE_KEY`
are set and `--audio` is on; any setup problem logs `FAMILY_NOTES_OFF` and the pipeline starts
normally.

- **Idle notes:** spoken when there is no find task, no pending request and no running agent
  task (`_family_idle`).
- **Object notes:** the first time a find task locks onto a labelled object
  (`_family_on_lock`), `notes:forObjectByDevice` is queried off the frame loop and up to two
  notes (warnings first) are spoken at once. Hand-guidance audio stays quiet while they play
  (`holding()`), at most 1.5 s for the lookup and 8 s for speech to start; haptics continue.
  Stop or a new request cuts the note. Re-finding the same object after a loss does not repeat it.
- `speech_adapters()` handles both players: `ClipPlayer` and `PriorityAudio` (`--agents`), whose
  `say()` takes a context instead of `is_current`.

`from_env` returns `None` when either variable is missing, so the feature is off by default.

## Validation

Convex: 17 simulated tests (`backend/tests/notes.test.ts`). Python listener: 12 tests with a
fake client, player and clock (`backend/tests/test_family_notes.py`). Not yet run on a live
deployment or the demo laptop. Known limits: audio URLs from file storage do not expire;
matching is lexical (no vector search yet); `crypto.subtle` hashing in queries is verified
in tests, not yet on a deployment.
