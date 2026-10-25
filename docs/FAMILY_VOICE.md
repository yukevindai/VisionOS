# Family Notes and Family Voice

*VisionOS connects people across the world, starting with the people closest to you: your family.*

## The idea

VisionOS guides a blind user's hand to the objects around them, using a camera, voice and
buzzing wristbands. For that user the voice **is** the interface. Most of what they hear is a
synthetic assistant. Family Notes adds the people who know their home best.

> Lan lives in another city. On her phone she writes one line: *"The bottle on the desk is your
> medicine water, not drinking water."* She records twenty seconds of her own voice, once.
>
> At home, her dad says *"Find the bottle."* The moment VisionOS locks onto it, before the
> wristband starts guiding his hand, he hears **Lan's own voice**: *"Safety note from Lan: the
> bottle on the desk is your medicine water."* Lan's phone shows **Heard at 9:42**.

Three things make this more than a messaging app:

1. **Right moment.** Notes wait while VisionOS is guiding, and an interrupted note is spoken
   again. A note about an object plays when the user finds that object, so a warning arrives
   before the hand gets there.
2. **Right voice.** With Lan's consent, notes are read in her voice through Cartesia Instant
   Voice Clone. A family member's voice carries trust and warmth that a stock voice cannot.
3. **Closed loop.** Lan sees live when her note was heard. Notes she set to be checked again
   ("Check again in 30 days") are read as *"may be out of date"* until she confirms them.

## How it fits together

```
Helper's phone (apps/family-notes)          Convex (backend/convex)                     User's laptop (backend/r3_vision)
  record consent ──── upload WAV ─────────▶ voicesNode:create ── Cartesia /voices/clone
  write note ──────── notes:create ───────▶ rate limit → insert → action-retrier render
                                            render ─ Cartesia /tts/bytes (helper voice,
                                                     owner's speed and volume) → file storage
  "Heard at 9:42" ◀── live query ──────────  notes:forDevice (live) ────────────────────▶ speaks when idle, plays the WAV
                                            notes:forObjectByDevice (search index) ◀──── "Find the bottle" locks → plays first
                                            notes:markHeardByDevice ◀─────────────────── after it played to the end
```

## Cartesia features used

| Feature | Where | Why it matters here |
|---|---|---|
| **Sonic TTS** (`/tts/bytes`, WAV) | `notesAudio.ts` renders each note once, in the background, under `@convex-dev/action-retrier` (timeouts, 429 and 5xx are retried) | The laptop and web page play it instantly, with no wait at the moment the user needs it |
| **Instant Voice Clone** (`/voices/clone`) | `voicesNode.ts`, from a 10 to 60 s recording made in the page | Notes are spoken in the helper's own voice |
| **Delete voice** (`DELETE /voices/{id}`) | `voicesNode:remove` | Consent can be withdrawn: the voice is deleted at Cartesia first, then every note that used it is re-rendered |
| **`generation_config` speed and volume** | Owner's "How notes sound" (0.6–1.5×, 0.5–2.0×) | Older listeners and noisy rooms: slower, louder speech is an accessibility setting |
| **Per-request `voice.id`** | Helper voice per note, default voice for everything else | The cloned voice never speaks VisionOS guidance or anyone else's words |

## Responsible use

Cartesia's clone documentation does not set consent rules, so we built them in:

- **The consent is in the recording.** The helper reads aloud: *"My name is Lan. I agree that
  VisionOS may make a copy of my voice from this recording, only to read my own notes to my
  family. I can delete it at any time."* That recording is the only input to the clone, and the
  same sentence is stored with the voice and in the Cartesia voice description.
- **Microphone only.** Recording happens in the page; there is no file upload, so nobody can
  clone a voice from someone else's audio clip. 3 recordings per helper per hour.
- **The recording is deleted** right after cloning, whatever the outcome. Only the voice ID remains.
- **Narrow use.** The voice reads only that helper's own notes, always introduced as *"Note from
  Lan"* or *"Safety note from Lan"*. It never speaks hand guidance, hazards, payment or system messages.
- **Two off switches.** The helper can delete their voice. The user can turn helper voices off in
  "How notes sound". Removing a helper ends all of their VisionOS access.
- **Honest labels.** The user's note list says *"in Lan's own voice (AI copy, with consent)"*.
- **Notes are information, never permission.** A note cannot start guidance, confirm a press or
  approve a payment. Reassurance found by object matching is said as *"According to Lan's note"*,
  never as fact.
- **No secrets in the repo.** `CARTESIA_API_KEY` lives only in the Convex deployment environment;
  the laptop's device key lives only in its `backend/.env`.

## Setup

1. Convex deployment environment (once):
   ```sh
   cd backend
   pnpm exec convex env set CARTESIA_API_KEY <key>
   pnpm exec convex env set CARTESIA_VOICE_ID <default voice id>
   pnpm exec convex env set CARTESIA_MODEL sonic-3.5
   pnpm exec convex dev --once --typecheck=disable
   ```
2. Web page: see [apps/family-notes/README.md](../apps/family-notes/README.md) (Auth0 SPA client, `pnpm dev`).
   The helper uses **I'm helping someone → Read your notes in your own voice**; the user uses
   **Notes for me → How notes sound**.
3. Laptop: pair it under **Notes for me → Devices**, then set `CONVEX_URL` and
   `FAMILY_NOTES_DEVICE_KEY` in `backend/.env` and `pip install -r requirements-family-notes.txt`.
   See [FAMILY_NOTES.md](FAMILY_NOTES.md).

## Known limitations

- **English only for now.** Clones are recorded as `en`. Cartesia can make one voice speak other
  languages (`locale`, Add Voice Accents), so Lan's voice could read to a grandparent in
  Vietnamese. That is the next step; we have not built it.
- **No speaker verification.** We cannot prove that the person recording is the account holder.
  The spoken sentence, the confirmation checkbox, the mic-only recording and the rate limit
  reduce misuse; they do not prevent it.
- **Re-render scope.** Changing speed or volume re-renders unheard notes (up to 50). Heard notes
  keep their audio. Deleting a voice re-renders every note that used it.
- **The laptop plays the WAV only when the words match.** A stale note ("may be out of date") or
  a reassurance match ("According to …") is spoken from text in the laptop's own voice.
- **Network required.** Without it, notes still arrive later and the laptop falls back to its own voice.
- **Model.** The deployment model is set by `CARTESIA_MODEL`. Cartesia keeps up to 60 s of clone
  audio only on Sonic 3.6 and newer; older models use the first 10 s.
- **Abandoned uploads.** If the page closes between the upload and the clone request, the recording
  stays in Convex storage, uncloned, until it is cleaned up.
- **Validation so far is simulated.** Backend tests stub Cartesia (no credits). The browser's WAV
  conversion was checked in headless Chromium. A live clone on the deployment and a run on the
  Windows demo laptop are still to be done.
