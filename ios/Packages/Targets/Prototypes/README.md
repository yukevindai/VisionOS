# Isolated Apple Vision Menu OCR proof

**Uncompiled, unwired proof.** This source is outside `Sources/` and is not included in the
Targets package or iPhone app. No manifest, Xcode project, capture owner, audio scheduler or
teammate implementation was edited. Windows cannot compile or execute Apple Vision here.

`MenuOCRProof.recognize` accepts an owner-supplied raw `CGImage`, its actual EXIF orientation,
frame ID and local monotonic capture timestamp. It opens no camera and calls no provider.
Choose `.fast` or `.accurate`; language correction is off. Actual candidate text is retained
exactly. Language hints are optional and must be verified for the target device/languages.

```swift
let page = try MenuOCRProof.recognize(
    image: rawCGImage, orientation: actualOrientation,
    frameId: capturedFrameId, capturedAtMonotonicMs: capturedUptimeMs,
    accuracy: .fast, recognitionLanguages: ["en-US"], coverage: .unknown)
let json = try JSONEncoder().encode(page)
```

Run this synchronous call off the main UI and camera delivery queue. Ownership and consent
remain with the existing capture owner. If the owner already oriented the pixels, pass `.up`;
do not apply the original orientation a second time. The proof applies no crop, resize or
perspective transform, and records raw dimensions, oriented dimensions, orientation and identity
crop. Vision bottom-left normalized boxes are converted to upright top-left coordinates.

`words` and `image.width/height` are shaped for the isolated Python `menu_reader` layout input:
exact token text, upright pixel `[x,y,width,height]` boxes, IDs and page ID. Whitespace tokens
preserve prices such as `$12.50`. Exact complete candidate strings are also retained in `lines`.
Tokens without valid word boxes are omitted from `words`, disclosed in warnings, and remain in
the preserved line text. An adapter must not silently discard those lines or claim full extraction.

Vision's candidate confidence is reported as `candidateConfidence` on each line, and as explicitly
named `lineCandidateConfidence` on spatial tokens. Individual word `confidence` is absent/null;
it is not fabricated by copying the line score. These scores are not calibrated probabilities
that dish/price associations are correct. `coverage` permits only `unknown` or owner-declared
`partial`, never automatic `complete`. OCR cannot find a menu region it never saw. No ingredient,
allergen, price, depth, guidance or completeness inference occurs here.

## Mac/iPhone validation procedure (not yet run)

1. On a Mac with an Apple SDK, typecheck this file without modifying the app:
   `xcrun swiftc -typecheck MenuOCRProof.swift -framework Vision -framework ImageIO -framework CoreGraphics`.
   Any SDK/API issues must be fixed before treating the proof as runnable.
2. In an isolated test target, load saved menu CGImages and pass their actual EXIF orientation
   and test frame metadata. Try all eight EXIF values, especially 90-degree and mirrored cases.
   Compare output boxes to the same visually upright images; avoid a second orientation pass.
3. Compare `.fast` and `.accurate` on the SAME saved inputs. Record first usable/complete OCR
   wall time separately, exact text error rate, missed lines, price association, memory and device.
   This proof returns after the whole submitted image finishes; it is not a streaming OCR demo.
4. Use printed, multi-column, small-font, incomplete, similar-name/different-price menus, plus a
   kiosk and elevator frame. Keep correct text/boxes as reviewed ground truth, not model output.
5. Only after isolated tests, ask the native capture owner to feed snapshots into a dedicated
   menu document adapter. Test on a real iPhone for glare, blur, orientation and language support.
   Reuse the existing speech scheduler through its owner; this proof does not play audio.

No compile, benchmark, model accuracy or physical iPhone pass is claimed by this proof.

## Apple API references

- [Recognizing text in images](https://developer.apple.com/documentation/vision/recognizing-text-in-images)
- [VNRecognizeTextRequest](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)
- [usesLanguageCorrection](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/useslanguagecorrection)
- [VNRecognizedText confidence](https://developer.apple.com/documentation/vision/vnrecognizedtext/confidence)
- [Bounding a recognized-text range](https://developer.apple.com/documentation/vision/vnrecognizedtext/boundingbox(for:))
- [Normalized Vision observation bounding box](https://developer.apple.com/documentation/vision/vndetectedobjectobservation/boundingbox)
- [VNImageRequestHandler](https://developer.apple.com/documentation/vision/vnimagerequesthandler)
