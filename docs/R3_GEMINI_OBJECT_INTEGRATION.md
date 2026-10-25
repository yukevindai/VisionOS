# Native Gemini object integration

October 24, 2026 · Native client and authenticated Convex action implemented; deployment and physical recognition validation remain separate gates.

Normal iPhone Call now sends the owner's object description and one actual camera JPEG through `GeminiObjectEvidenceClient` → `ConvexObjectClient` → authenticated Convex `vision:findObject`. There is no cup/can/bottle/bowl vocabulary filter. This supports open-ended identification requests; it does not guarantee every object can be identified or safely reached. The action calls Gemini directly from the Convex Node runtime using server environment settings. The existing Python desktop pipeline and `vision:readScreen` remain unchanged. Gemini returns semantic object evidence only; it never authorizes movement.

## Action contract

Use the existing authenticated Convex action transport and Auth0 owner identity, just as `vision:readScreen` does. Keep Gemini credentials on the server. Do not expose the old unauthenticated laptop `/v1/select` server to the phone.

Action arguments (synthetic example; JPEG payload abbreviated):

```json
{
  "imageBase64": "<base64 JPEG, at most 524288 decoded bytes>",
  "query": "find my silver keys",
  "frameId": "native-frame-example",
  "capturedAtMs": 1792800000000,
  "requestId": "request-example"
}
```

`query` is trimmed, nonempty, at most 500 UTF-16 code units and contains no control characters. Frame/request identifiers are opaque correlation values; echo them exactly. `capturedAtMs` is the original frame's UTC milliseconds, not the server clock. Native monotonic request expiry is local and never serialized as UTC. The client uses `POST /api/action` with the existing Convex JSON wrapper (`path`, `args`) and bearer token.

Return an action value with this shape (synthetic identification-only example; not live provider evidence):

```json
{
  "requestId": "request-example",
  "mode": "REAL_API",
  "status": "SELECTED",
  "selectedObjectId": "keys-1",
  "screenModel": {
    "schemaVersion": 3,
    "frameId": "native-frame-example",
    "capturedAtMs": 1792800000000,
    "panelType": "other",
    "availability": "available",
    "coordinateSystem": "normalizedImageTopLeft",
    "source": "visionModel",
    "controls": [{
      "id": "keys-1",
      "label": "silver keys",
      "type": "other",
      "region": {"x": 0.2, "y": 0.3, "width": 0.1, "height": 0.2},
      "legibility": "clear"
    }],
    "sections": [],
    "isPaymentScreen": false,
    "image": {"widthPx": 640, "heightPx": 480, "exifOrientation": 1, "orientationApplied": true},
    "evidence": {"provider": "gemini", "model": "<actual deployed model identifier>", "live": true}
  }
}
```

The schema-v3 `controls` collection is reused as a native adapter projection of object observations; it does not imply objects are buttons. Each selected object uses `type: "other"`, a unique ID, a bounded nonempty human-readable label (at most 160 UTF-16 units, no control characters), and `legibility: "clear"`. Here legibility indicates that R3 considers the semantic label clear; it does not require printed text. The selected ID must match exactly one observation. R3 selects by the supplied query, including synonyms/descriptions and questions such as “what is this?”; the iPhone does not perform another lexical match. Return ambiguity when the intended object cannot be determined.

Allowed statuses: `SELECTED`, `NOT_FOUND`, `AMBIGUOUS`, `PROVIDER_UNAVAILABLE`. Only `SELECTED` may carry `selectedObjectId`; omit it or set null otherwise. Semantic success/not-found/ambiguity must carry an actual live Gemini model envelope. The client rejects `SIMULATED`, missing live provenance, cross-frame/time/request responses and malformed JSON. For provider failure, prefer an authenticated action error; do not manufacture `live: true` evidence to fill the envelope. Both action errors and unusable/unavailable envelopes fail closed on the phone. Maximum encoded JSON response is 262144 bytes. No raw provider error or unbounded note is spoken.

Use coordinates relative to the orientation-applied image sent by the phone: top-left origin, each box fully within [0,1]. Echo the actual dimensions and image orientation metadata. Keep at most 100 unique object entries. If preprocessing resizes or crops, map coordinates back to that input image. Do not stamp a box from the request onto a newer frame.

## Identification and guidance are distinct

The R3 reader omits numeric confidence and safety. The client preserves those omissions: a clear selected label with a valid region becomes an `unassessedObject` candidate, never an ordinary/safe object. Explicit provider hazard classifications are rejected; invalid or reported low confidence is rejected. R3 must not invent numeric scores or mark all objects ordinary to enable guidance.

Native tracking is prepared immediately from the retained original camera image, without emitting movement cues. The user must confirm the intended object is safe to touch and within reach. That answer preserves unknown safety and requests fresh native reacquisition; it does not authorize cues by itself. Continuous tracking keeps the same candidate through confirmation. Target loss, Stop, context or camera-geometry changes invalidate that preparation.

Native R1 then requires current tracking, a visible selected hand, measured target and fingertip depth, offline Stop and output authority. Current observations must remain under 300 milliseconds old. Gemini does not authorize movement, clear hazards or confirm a grasp. Unknown temperature, sharpness, contents and weight remain unknown. No automatic approach or navigation is added.

Object-recognition tasks and their HTTP requests expire after 60 seconds. Other task requests retain their five-second deadline; user confirmation remains 30 seconds. This longer semantic wait does not extend native source retention or the 300 ms physical-evidence limit: a result whose original capture can no longer be safely reacquired cannot start guidance. A new task generation waits for its own real camera frame. Stop, End, task replacement and camera-geometry changes cancel pending work and reject late results, including providers that ignore cancellation. Stored identity is checked before and after the request. Local camera/Stop remain available without cloud configuration; normal Call does not silently fall back to the four-category local detector.

## R3 handoff and phone acceptance

1. Review `vision:findObject` and `convex/lib/objectVision.ts`. The action checks the existing authenticated account before image/provider work and implements the argument/result shape above.
2. Deploy the Convex action to the intended development deployment. Set `GEMINI_API_KEY`, `GEMINI_MODEL`, and optionally `GEMINI_THINKING_LEVEL` (`low`, `medium`, or `high` on compatible Gemini 3 models) in its server environment. There is no model fallback. Object recognition does not require the separate Python service; the existing screen-reader action still does. Supply the real public Convex host, Auth0 domain and native client ID for ignored `ios/Config/Local.xcconfig` using `Local.example.xcconfig`; do not put a Gemini key on iPhone.
3. Sign in through the existing image-reader or friend tools. The object client reuses the stored account without opening a login prompt mid-request. Signed-out and expired sessions return an actionable sign-in message.
4. Integrate the separately reviewed launch and immediate-Call fixes ([PR10](https://github.com/yukevindai/VisionOS/pull/10), [PR15](https://github.com/yukevindai/VisionOS/pull/15)) before replacing the working phone build. This PR starts from main and does not duplicate those changes.
5. On the real phone, request visible keys, a phone, a book and less common objects; compare spoken identification with the scene. Exercise multiple matches, missing objects, outage/sign-out, rotated camera, Stop during a slow response and task replacement. Confirm original-frame reacquisition and all physical guidance requirements separately. No real-device or Gemini accuracy pass is claimed by mocked tests.

The direct action has a 60-second total provider deadline, including response-body reads. The native 60-second deadline also includes upload and transport time, so it can expire before the server deadline. It accepts upright JPEGs, obtains dimensions from the JPEG rather than asking Gemini to guess them, and rejects rotated EXIF. Output is bounded to 20 matching objects and 256 KiB. The prompt and response whitelist omit confidence and safety; successful recognition can propose an unassessed object for explicit confirmation and native tracking.

Offline tests use explicitly simulated HTTP replies. A compiled client, passing mocked tests, and a direct provider smoke test do not establish authenticated Convex deployment or real-device recognition accuracy.

## Direct answers to questions

The optional `answerQuestion: true` action argument switches Gemini from object selection to direct answering. Questions such as “what is in front of me”, “where are my keys”, “how many bottles”, and “is the bottle empty”, plus describe/explain requests, take this path. A scene with multiple objects is described directly rather than treated as an ambiguous target. General questions can use relevant general knowledge, distinguished from visible evidence; uncertain or unavailable facts are stated declaratively without a clarification question.

The response carries a bounded `answer` string and the existing correlated live camera envelope with `status: NOT_FOUND`, null selection, and empty controls. Here NOT_FOUND means no movement target is selected, not that the answer failed. Native code validates provenance and bounds, then finishes with informational `answered` status, unverified physical result, and speech only. It never prepares native tracking, guides, asks a grasp confirmation, or emits physical success feedback. New questions replace pending action confirmations instead of being swallowed. Explicit find/guide/pick requests retain the separate guidance path and its physical gates.

Backend validation rejects question marks and common clarification requests, including unpunctuated “please specify” replies. Provider failure produces a declarative inability statement, never an invented answer. These guards prevent the observed clarification loop; model factual accuracy remains a live-device acceptance item.
