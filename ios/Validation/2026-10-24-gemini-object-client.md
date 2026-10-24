# Native Gemini object client validation — October 24, 2026

## Change and boundaries

Normal native Call composes `GeminiObjectEvidenceClient` using the proposed authenticated Convex `vision:findObject` action. The owner-utterance four-noun filter is removed. The previous local YOLO adapter stays in source/tests but is no longer composed into normal Call. Missing service setup, sign-in, ambiguity, absence and identification-only results produce bounded spoken/displayed explanations. Original-frame identity, context/geometry cancellation, request deadlines, local Stop, R1 reacquisition and physical evidence gates remain intact.

R3 retains backend and Targets package ownership. No backend/deployment/shared-schema/provider-key changes are included. The API proposal and synthetic request/response example are in [the R3 handoff](../../docs/R3_GEMINI_OBJECT_INTEGRATION.md). The endpoint and real native service configuration are not available in this checkout; live Gemini behavior and accuracy are unverified.

## Actual local results

Xcode 27.1 (27A9275), iPhone 17 Pro simulator with iOS 26.5:

- Final app unit target: **188 passed, 0 failed**, `/tmp/reflex-gemini-final-app-tests.log`; result bundle `Test-Reflex-2026.10.24_13-55-11--0700.xcresult` under the local derived-data test logs.
- Eight new HTTP-boundary tests cover query/frame forwarding, authentication/account changes, malformed/simulated/correlated responses, input/response bounds, timeout and cancellation.
- Eleven new evidence tests cover arbitrary keys/phone/book labels, original-frame crop mapping, missing/low/invalid confidence and safety, ambiguity/not-found, configuration, generation/geometry/caller cancellation, source freshness and expiry.
- Five new coordinator tests cover arbitrary owner queries, spoken/displayed failures without guidance, Stop and mute precedence, and late failure suppression after Stop.
- Swift packages: **167 passed** (Guidance 11, Targets 20, Tasks 92, HapticLink 44); logs `/tmp/reflex-gemini-{Guidance,Targets,Tasks,HapticLink}.log`.
- Unsigned Release iPhone build: **passed**, `/tmp/reflex-gemini-release-build.log`. This is compilation, not installation or launch.
- Project regeneration is deterministic; `git diff --check` passes.

All model/HTTP evidence in the new tests is synthetic, including live-provenance fields deliberately used to exercise validation. These tests are not real-provider or physical-guidance acceptance.

## Remaining acceptance

No UI/accessibility suite or live Gemini call was run for this change. The three previously documented contrast audit failures remain unsuppressed. Required-check success, when available, must not be presented as a full iOS CI or live-device pass.

The user's connected phone retains the separately installed startup-fixed build. This branch starts from main and does not include the unmerged framework-runpath PR10 or immediate-Call PR15. Integrate those PRs before installing a combined build; the Gemini client was not installed over the working app. R3 must implement/deploy the action and provide the real public service configuration before on-phone object-recognition testing. The phone's previous camera/depth and successful Call observations do not validate this Gemini change.
