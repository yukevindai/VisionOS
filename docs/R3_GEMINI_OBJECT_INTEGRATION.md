# Native Gemini object integration proposal

October 24, 2026 · R2 client implemented; R3 action/deployment pending.

Normal iPhone Call now sends the owner's object description and one actual camera JPEG through `GeminiObjectEvidenceClient` → `ConvexObjectClient` → authenticated Convex `vision:findObject`. There is no cup/can/bottle/bowl vocabulary filter. This supports open-ended identification requests; it does not guarantee every object can be identified or safely reached. R3 retains backend, provider prompts, target interpretation and deployment ownership. This document proposes the additional action; it does not assert that the current backend implements it. No shared schema or R3-owned code is changed in this PR.

## Proposed action

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

The existing R3 reader omits numeric confidence and safety. This client preserves those omissions. A clear selected label can be announced as identification-only; it cannot start hand guidance without a valid region, explicit confidence in [0.6,1], and explicit `safety: "ordinary"`. Invalid confidence is rejected and reported low confidence is never announced as a confident identification. R3 must not invent numeric scores or mark all objects ordinary to satisfy the gate. Establish any interpretation/calibration with the owners first.

Even a semantically eligible result only proposes a target. Native R1 must reacquire it using the retained original camera frame, current tracking, visible hand and usable depth. Existing freshness, history limits, offline Stop and output-authority checks remain in place. Gemini does not authorize movement, clear hazards or confirm a grasp. Unknown temperature, sharpness, contents and weight remain unknown. No automatic approach or navigation is added.

Requests expire with the existing task deadline (five seconds); the HTTP request also has a five-second timeout. A new task generation waits for its own real camera frame. Stop, End, task replacement and camera-geometry changes cancel pending work and reject late results, including providers that ignore cancellation. Stored identity is checked before and after the request. Local camera/Stop remain available without cloud configuration; normal Call does not silently fall back to the four-category local detector.

## R3 handoff and phone acceptance

1. Implement/review `vision:findObject` with the argument/result shape above and actual query-aware Gemini object interpretation. Reuse authenticated backend boundaries; the existing `vision:readScreen` panel-only action is insufficient.
2. Deploy the protected Convex/private vision services and configure provider credentials server-side. Supply the real public Convex host, Auth0 domain and native client ID for ignored `ios/Config/Local.xcconfig` using `Local.example.xcconfig`; do not put a Gemini key on iPhone.
3. Sign in through the existing image-reader or friend tools. The object client reuses the stored account without opening a login prompt mid-request. Signed-out and expired sessions return an actionable sign-in message.
4. Integrate the separately reviewed launch and immediate-Call fixes ([PR10](https://github.com/yukevindai/VisionOS/pull/10), [PR15](https://github.com/yukevindai/VisionOS/pull/15)) before replacing the working phone build. This PR starts from main and does not duplicate those changes.
5. On the real phone, request visible keys, a phone, a book and less common objects; compare spoken identification with the scene. Exercise multiple matches, missing objects, outage/sign-out, rotated camera, Stop during a slow response and task replacement. Confirm original-frame reacquisition and all physical guidance requirements separately. No real-device or Gemini accuracy pass is claimed by mocked tests.

The phone retains the startup-fixed build while this client awaits R3 deployment/configuration. R2 has not deployed or modified `backend/` or `ios/Packages/Targets/` and has not messaged the teammate on the user's behalf.
