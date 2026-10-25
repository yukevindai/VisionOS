# Menu, kiosk and elevator technology research

Research date: October 24, 2026. Primary sources only. No packages or models installed for this
research. Published capabilities and vendor benchmarks below are not Reflex validation or a
promise of latency/accuracy on the demo phone. Current repository features are audited separately.

## Practical recommendation

1. **iPhone: Vision OCR on snapshots from R1's existing capture owner.** Extract text locally,
   preserve recognized wording and word/line geometry, then parse layout. Use accurate recognition
   for a committed high-resolution menu capture; evaluate fast recognition only for lightweight
   framing feedback. Do not create a second camera session or change R1's guidance loop.
2. **Windows demonstration: use the OCR engine already available, with a replaceable adapter.**
   The parent implementation has successfully probed Windows.Media.Ocr/en-US on this host. Treat
   that as an experimental current-host adapter, not a general supported unpackaged deployment:
   Microsoft documents desktop support as requiring MSIX/package identity. Preserve unknown
   confidence as null; do not invent per-word confidence. PaddleOCR is the next benchmark
   candidate in a separate environment, not a prerequisite for today's small prototype.
3. **Menu memory and most questions should be local.** Reading a section, listing recognized
   prices and computing a cheapest recognized option need no image resubmission. Use Gemini
   text calls for harder questions grounded in retained source excerpts; use a selective image
   crop only when text/layout evidence is insufficient.
4. **Reuse existing native speech and output scheduling.** Add menu reading as resumable chunks
   with a cursor and priority below hand guidance. Stop and interruption must cancel queued menu
   chunks, not just pause their audio. Keep the saved menu after the camera points elsewhere.
5. **Extend elevator/kiosk interpretation rather than replacing it.** OCR can supply labels;
   existing selection/state safety still decides ambiguity and payment boundaries. Label text
   location is not automatically the pressable control, and a 2D region is not safe 3D guidance.

## OCR and visual-understanding comparison

| Technology | Platforms, offline and dependencies | Accuracy/speed evidence | License and fit |
| --- | --- | --- | --- |
| **Apple Vision, VNRecognizeTextRequest** | Native iOS/macOS framework, on-device, no bundled OCR model or Python runtime. Recognition results provide candidates and geometry. | Apple distinguishes fast and accurate paths; no universal milliseconds or menu accuracy guarantee. Benchmark the actual iPhone, font sizes, language and capture resolution. | Apple SDK/platform terms, not an open-source OCR package. Best fit for the native iPhone architecture. [Vision text recognition](https://developer.apple.com/documentation/vision/recognizing-text-in-images), [recognition level](https://developer.apple.com/documentation/vision/recognizetextrequest/recognitionlevel-swift.property). |
| **Apple VisionKit DataScannerViewController** | Native camera scanning UI with text/code recognition, highlighting, focus/zoom and framing feedback. Requires device support (A12+) and camera availability checks. | Quality settings trade resolution/speed; high-frequency geometry tracking does not imply full OCR on every frame. No comparable menu benchmark published in the cited guide. | Apple SDK terms. Useful as an exclusive scanning screen; it owns a camera UI, so do not run it beside R1's AR capture. Prefer Vision on R1 frames when sharing the active session. [Scanner guide](https://developer.apple.com/documentation/visionkit/scanning-data-with-the-camera?changes=l_7), [support check](https://developer.apple.com/documentation/visionkit/datascannerviewcontroller/issupported). |
| **Google ML Kit Text Recognition v2** | On-device iOS/Android SDK; no Windows Python implementation. iOS assets are statically linked, about 38 MB per script SDK in the cited guide. Requires corresponding native integration. | Google describes Latin recognition as real-time on most devices and other scripts as slower. It recommends enough character pixels and focus; this is not a measured Reflex latency or menu-pairing accuracy. | Google API/ML Kit terms rather than an Apache-licensed SDK. Processing stays on-device; SDK metrics/update communication still needs disclosure consideration. Alternative when supported scripts or shared Android implementation justify the extra SDK. [iOS guide](https://developers.google.com/ml-kit/vision/text-recognition/v2/ios), [terms/privacy](https://developers.google.com/ml-kit/terms). |
| **PaddleOCR** | Python/C++ Windows/Linux deployment; offline after downloading the chosen models. PaddlePaddle and model assets are additional dependencies. Native/mobile deployment is a separate engineering path, not a drop-in iOS Swift framework. | Official PP-OCRv5 report: mobile pipeline averaged **1.75 s/image CPU** on a Xeon Gold 6271C test environment; 200 general/document images, disk reading included. That is not this laptop, a cold-start time or full-menu semantic accuracy. Component-only timings must not be reported as total menu latency. | Repository Apache-2.0; retain attribution and verify individual chosen assets. Strong optional Windows baseline; pin version/config because current documentation also discusses newer model families. [Project/license](https://github.com/PaddlePaddle/PaddleOCR), [installation](https://paddlepaddle.github.io/PaddleOCR/main/en/quick_start.html), [official PP-OCRv5 benchmark](https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5.en.md). |
| **Windows.Media.Ocr** | Windows OS OCR with installed recognition languages. Current-host probe succeeded. Official desktop support requires package identity/MSIX; an unpackaged CLI experiment is not a supported deployment promise. | Benchmark locally. API words/lines/rectangles are useful layout inputs; a missing confidence field must remain unknown. No comparable universal menu result is claimed. | Windows platform/API terms. Useful zero-model-install experiment here; preserve an adapter boundary for Paddle/native Vision. [Microsoft namespace documentation](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr?view=winrt-26100). |
| **Gemini image understanding** | Cloud API from Windows or iOS backend; requires network, key, billing/access and request controls. Supports image interpretation, detection and visual Q&A. | No latency guarantee for this menu task. Benchmark the same image/resolution/model/effort; separate upload, inference and parsing. Previous Reflex timings do not prove a faster or slower new configuration. | Hosted-service terms, not offline/open-source weights. Use for ambiguous relations and grounded follow-ups, not every visible text frame. [Image-understanding guide](https://ai.google.dev/gemini-api/docs/image-understanding). |
| **YOLO-World** | Open-vocabulary object/region detection. Python/weights are feasible on Windows; iOS requires export/runtime validation and benchmarking. Cached vocabulary can avoid encoding prompts every frame. | Its published detector results do not measure character recognition, name-price pairing or this phone. It is not OCR and cannot replace text extraction. | Ultralytics implementation is AGPL-3.0; commercial licensing alternatives require separate consideration. Optional menu/kiosk region locator only, not necessary for this MVP. [Model guide](https://docs.ultralytics.com/models/yolo-world/), [implementation license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE). |

No head-to-head Apple/Paddle/Gemini accuracy or latency measurement was performed by this
research subtask. A local OCR confidence score is not automatically a probability that a dish
and its neighboring price belong together. That association needs its own evidence and tests.

## Voice: start with a grounded local controller, not a second audio owner

| Pipeline | Benefits | Complexity, latency and limits |
| --- | --- | --- |
| **Existing Apple Speech + menu controller + AVSpeechSynthesizer** | Native microphone/STT/TTS reuse; local reading, section navigation and interruption can work without a cloud model after capture. AVSpeechSynthesizer exposes pause, resume and stop. | Maintain utterance/cursor state and audio cancellation through the existing scheduler. SFSpeechRecognizer is offline only when the recognizer supports it and the request requires on-device recognition. No universal recognition/TTS latency numbers are claimed. Test barge-in with speaker echo and real restaurant noise. [Apple Speech capability](https://developer.apple.com/documentation/speech/sfspeechrecognizer/supportsondevicerecognition), [TTS controls](https://developer.apple.com/documentation/AVFAudio/AVSpeechSynthesizer?changes=latest_mi__9__5). |
| **Apple SpeechAnalyzer / SpeechTranscriber** | Apple's newer iOS 26 on-device speech path supports streaming/long-form transcription and managed language assets. | Optional newer-OS enhancement; keep existing deployment-target fallback. Requires asset availability and a device/locale test. Apple's qualitative improvement claim is not a numeric Reflex benchmark. [Official WWDC introduction](https://developer.apple.com/videos/play/wwdc2025/277/). |
| **Gemini Live** | Streaming voice/vision, server conversation state, input/output transcripts and barge-in support. | Needs stateful WSS, microphone/audio-format conversion, interruption events, session reconnect/security and centralized audio ownership. Network-dependent; no guaranteed time to first spoken word. It is a separate Live API/model from normal Gemini image generateContent. Do not transfer Flash thinking settings blindly: documented Live model variants have different setup support. [Overview](https://ai.google.dev/gemini-api/docs/live-api), [capabilities/model differences](https://ai.google.dev/gemini-api/docs/live-api/capabilities). |

For the menu demo, the simpler pipeline has fewer new failure modes: local STT recognizes
read/stop/pause/resume/next commands; deterministic code handles price/list questions; Gemini
receives only the grounded menu facts for unsupported questions; existing TTS speaks accepted
facts. This is an engineering recommendation, not a measured claim that local STT always beats
Live. Compare both with turn-end-to-first-audio p50/p95, interruption-to-silence, correct facts,
restaurant noise and network-loss tests. Conversation and hand guidance need separate logical
queues but one R1-owned output scheduler. A hazard/stop cancels menu speech immediately.

Order communication should produce a draft from the user's selected items. Read back the draft
privately; speak it toward staff only after explicit user direction/confirmation. Never submit
an order, authorize a charge or activate kiosk purchase controls as a side effect of conversation.

## Existing products: menu reading and visual conversation are established

| Reference | What is already publicly documented | Specific lesson for Reflex |
| --- | --- | --- |
| **Microsoft Seeing AI** | Short-text reading, guided document capture, formatting-aware document speech and document chat, explicitly including menu questions. | Do not claim menu OCR or menu Q&A as novel. Compete on demonstrated completeness/provenance, controlled reading and reliable interaction transitions. [Microsoft feature announcement](https://blogs.microsoft.com/accessibility/seeing-ai-app-launches-on-android-including-new-and-updated-features-and-new-languages/). |
| **Google Lookout** | Text/documents, framing guidance and reading tools; Google's design account also describes image follow-up questions powered by Gemini. | Use capture guidance, adjustable reading and explicit uncertainty. Continuous repeated narration is unnecessary once a usable document is retained. [User guide](https://support.google.com/accessibility/android/answer/9031274?hl=en), [Google design account](https://design.google/library/google-lookout-design-accessibility). |
| **Gemini Guided Vision** | Google announced accessible conversational visual assistance in Gemini Live on compatible Android devices on October 1, 2026. It explicitly includes reframing cues. | Voice-first camera interpretation already exists. Reflex's proposed value must be specific and measured; do not imply this is a safe-travel replacement. [Google announcement](https://blog.google/innovation-and-ai/products/gemini-app/guided-vision-gemini-live/). |
| **Be My Eyes / Be My AI** | Photo descriptions, follow-up questions, additional pictures, screen-reader support and escalation to a sighted volunteer. Current help says internet is required and supports iOS/Android/Windows. | Retain conversational context and offer human help when evidence is insufficient. Do not infer an always-offline competitor comparison. [Official help](https://support.bemyeyes.com/hc/en-us/articles/18133134809105-Using-Be-My-AI). |
| **GoodFoodTalks** | Accessible restaurant menus through its site and mobile app, supplied for participating venues. | A restaurant-provided accessible menu can be more reliable than a camera scan. Offer it as an optional source when explicitly identified, but never silently combine stale online prices with a photographed menu. [Official service](https://goodfoodtalks.com/). |
| **Accessible kiosk systems** | Storm describes AudioNav audible/tactile work with McDonald's; Freedom Scientific publishes a JAWS kiosk walkthrough. SiteKiosk documents screen-reader/magnification and keyboard navigation options. | Prefer the kiosk's built-in accessible path when available. Camera-based button guidance is a fallback, not a replacement for semantic screen-reader navigation. These deployments are not proof every venue has an accessible kiosk. [Storm](https://www.storm-interface.com/news/storm-interface-working-with-mcdonalds-to-deliver-more-accessible-customer-experience/), [JAWS walkthrough](https://blog.freedomscientific.com/wp-content/uploads/2021/10/FSCast206Transcript-October2021.html), [SiteKiosk](https://www.sitekiosk.com/how-tos/configuring-accessibility-in-sitekiosk-online/). |

The opportunity is not a new ability to read text aloud. It is an auditable combined experience:
capture a usable multi-page menu, retain exact source evidence, navigate it conversationally,
state when the view is partial, and move into physical interaction only when independent local
tracking/depth validation permits it. These are proposed Reflex outcomes, not proven competitor
gaps or verified user-study conclusions. Co-design and real blind/low-vision user testing remain
necessary to establish which differences actually help.

## Full-menu design and extension plan

**Capture:** guide the user to include the page boundary and enough character detail. Commit
high-resolution stills rather than repeatedly asking a cloud model to read low-resolution
preview frames. Preserve source/capture ID, orientation/crop transforms and OCR raw output.
An initial overview can identify likely sections while closer shots recover small text. Use
separate pages for distant boards; do not require all detail in a single image.

**Parse:** assign columns/reading order before associating same-row names and prices. Keep
descriptions, size/option labels, currencies and price qualifiers attached to their source spans.
Ambiguous price-to-item relationships remain unresolved. Never repair a price or ingredient
using common sense. The menu title, category and allergen claims need visible supporting text.

**Merge:** deduplicate only supported overlapping capture regions or explicit same-page matches.
The same dish name with a different size/price may be a legitimate separate item. Preserve
conflicts and alternate observations. Maintain page/region coverage plus an explicit partial or
unknown-completeness state. More recognized text is not proof the entire menu was captured.

**Answer/read:** index the retained structure and source spans. Read section names first and let
the user choose full reading or a section. Cheapest means cheapest reliably recognized relevant
option in the same currency/price basis, not cheapest on an unseen part of the menu. If a menu
does not explicitly establish peanut/allergen facts, say that it does not confirm them and suggest
asking staff; missing words never establish allergen absence. Cache the menu for the session,
including after the camera moves away. Resume/skip operate on a reading cursor, not a new scan.

**Kiosks:** extend the current ScreenModel/TargetSelector and task-state machinery. OCR supplies
candidate strings; layout parsing must still bind each string to the actual button/card. Store
screen identities and reread after user interaction. Pixel/OCR changes indicate change, not proof
that a purchase succeeded. New/changed screens invalidate old target regions and guidance.
Payment, unlabeled/ambiguous and emergency controls stay blocked.

**Elevators:** OCR can quickly read floor labels while Gemini resolves unusual label/control
associations. Keep repeated labels as multiple candidates; never choose a random duplicate.
Verify glare, reflections, braille plates, arrow-only controls and emergency/alarm controls in a
dedicated real-photo set. Existing selection logic can be extended, but OCR alone neither maps
text to a physical press surface nor establishes a current 3D location.

**Evaluation:** publish word/character error, item recall, invented items, exact name-price pairs,
section grouping, answer correctness and completeness behavior. Measure cold/warm time to first
readable text and complete structure, memory/CPU, and voice interruption latency separately.
Generated clean menus are useful controlled tests, not evidence of restaurant/elevator/kiosk
field performance. Apple OCR and any Paddle/Gemini baseline require matched real-image tests
before claiming superiority. No large model installation is needed merely to perform research.
