# Isolated cached-menu speech adapter

`backend/menu_reader/speech_adapter.py` is a small adapter, not a new audio engine. It
imports only the menu question/reading module plus Python standard-library types. It never
imports Claude's audio, live tracking, ALL OBJECTS, hand or LiDAR code and creates no worker,
camera, OCR scan, provider connection or automatic playback. Their FPS paths are unchanged.

## What works now

- `MenuSpeechAdapter` holds a `MenuSession` over the already-cached menu. Follow-up questions
  call that local session; they cannot trigger rescanning or repeated Gemini calls.
- `MenuSpeechRequest` carries text, source IDs, deterministic menu ID, unique event ID,
  actual owner session/task generation, independent reading-generation token, priority **4**,
  audience, confirmation state and short queue lifetime. These are historical menu facts,
  not fresh physical-guidance observations.
- `start_reading` and `prepare_next` produce one organized chunk at a time. Preparation does
  not speak. `release` explicitly forwards one valid request to an injected scheduler. It
  rejects duplicates, stale/canceled generations, modified requests and expired queue entries.
- `completed(event_id)` advances a reading chunk only after the real scheduler reports that
  exact event finished. No timer or enqueue acknowledgment pretends speech was completed.
- Pause/stop/cancel invalidate pending menu requests. Resume restarts only an interrupted
  chunk, with a new event ID; it does not automatically replay the whole menu. Skip is explicit.
- An order draft can be prepared for the user, but staff-directed requests require an explicit
  trusted `staff_confirmed=True` confirmation, then a separate explicit release. This adapter
  never places an order, spends money, taps a button or automatically speaks to staff.

## Runnable callback binding

```python
from menu_reader.speech_adapter import CallbackScheduler, MenuSpeechAdapter

# These callbacks belong to the existing speech owner. The adapter does not implement audio.
scheduler = CallbackScheduler(existing_owner_enqueue, existing_owner_cancel_menu)
adapter = MenuSpeechAdapter(cached_menu, owner_session_id, owner_generation, scheduler)

answer, request = adapter.prepare_answer("Which is cheapest?")
payload_ready_for_speech = request.to_dict()
# Only after the user requested spoken output:
adapter.release(request)

adapter.start_reading("BURGERS")
chunk = adapter.prepare_next()
adapter.release(chunk)
# Only the scheduler's actual finish event calls this:
adapter.completed(chunk.event_id)
```

For an entirely silent demonstration, omit the scheduler and serialize `request.to_dict()`.
This is sufficient to produce a response ready for the current speech owner without replacing
its implementation. The injected enqueue callback returns whether it accepted the request;
it must not be treated as an audible-completion signal.

## Exact native interface mapping and remaining iOS-owner work

The actual native protocol is [`SpeechSchedulerClient`](../ios/App/Integration/AdapterProtocols.swift#L40):
`speak(text, priority, context)`, `cancel(context)` and `localStop(context)`. `SessionContext`
contains `sessionID` and owner `generation` in
[`SessionState.swift`](../ios/App/Session/SessionState.swift#L3).

1. The owner bridge validates `session_id`/`generation` against the **current** native context,
   keeps `reading_generation` as a menu-only cancellation token, and maps valid user-audience
   requests to `speech.speak(request.text, priority: 4, context: currentContext)`. Staff audience
   is allowed only after the explicit trusted confirmation represented by this request.
2. Python and iPhone monotonic clocks are not interchangeable. The DTO's 30-second queue
   lifetime is local to its producer; a network/native bridge must validate transport/session
   state and re-establish queue lifetime on its own clock. Never use it to refresh a menu box
   into the 300ms physical-guidance evidence channel.
3. **The current native protocol has no speech-completion callback or append queue.**
   [`NativeAudioCoordinator.speak`](../ios/App/Audio/NativeAudioCoordinator.swift#L201) and
   `beginSpeech` replace existing output. Sending all chunks in a loop would interrupt every
   earlier chunk, potentially leaving only the last one audible. Release one chunk at a time.
4. The iOS owner must expose a completion/cancellation observation for its own menu utterance
   token. Its existing private
   [`speechSynthesizer(...didFinish:)`](../ios/App/Audio/NativeAudioCoordinator.swift#L315)
   already knows when an active native utterance actually finishes. Do not replace the
   synthesizer delegate or infer completion from captions/timers. Until such owner binding
   exists, the safe runnable mode is explicit user-driven chunk release, not automatic reading.
5. `cancel_menu` is deliberately scoped to a particular menu generation. Native
   `cancel(context:)` cancels whatever current output belongs to that context; a bridge must
   only call it when that output is the menu request it owns. It must not stop a higher-priority
   hazard/hand-guidance utterance. Global user Stop continues through the existing coordinator's
   local-stop route; this module does not impersonate it or produce guidance cues.

No Swift, Xcode project, native scheduler, service endpoint or existing Python audio file was
edited. **The adapter is executable and tested with callbacks, but it is not directly wired
from this desktop Python process into the installed iPhone app.** No new speaker/device pass
is claimed. Claude's fixed short-word hand-guidance player is not an arbitrary menu narrator.

## Focused verification

```powershell
python -m unittest discover -s tests/menu_reader -p test_speech_adapter.py -v
```

Seven tests passed using a SIMULATED menu and injected scheduler: typed dispatch/provenance,
completion-driven cursor, canceled generations/context changes, resume/skip behavior, cached
follow-up Q&A with OCR/network functions blocked, staff confirmation and stale/modified request
rejection. No real audio, camera, cloud request or physical-iPhone test was run.
