// ISOLATED PROOF: not compiled, tested, wired to the app, or included in the Targets target.
// Receives an existing image from its owner. No camera, speech, provider or guidance authority.
import CoreGraphics
import Foundation
import ImageIO
import Vision

@available(iOS 13.0, macOS 10.15, *)
public enum MenuOCRProof {
    public enum Accuracy: String, Codable { case fast, accurate }
    public enum Coverage: String, Codable { case unknown, partial }
    public enum InputError: Error { case invalidFrameMetadata }

    public struct Region: Codable {
        public let x: Double, y: Double, width: Double, height: Double
        fileprivate func pixelBox(_ w: Int, _ h: Int) -> [Double] {
            [x * Double(w), y * Double(h), width * Double(w), height * Double(h)]
        }
    }
    public struct Word: Codable {
        public let id: String, text: String, pageId: String, lineId: String
        public let region: Region
        /// Upright image pixel x,y,width,height, matching menu_reader/layout.py input.
        public let box: [Double]
        /// Vision does not supply independently measured word confidence through this API.
        public let confidence: Float?
        /// Confidence of the full recognized-text candidate, NOT this individual word.
        public let lineCandidateConfidence: Float
    }
    public struct Line: Codable {
        public let id: String, text: String
        public let region: Region?
        public let candidateConfidence: Float
        public let confidenceScope: String
    }
    public struct ImageMetadata: Codable {
        public let width: Int, height: Int
        public let originalSize: [Int]
        public let exifOrientation: UInt32
        public let orientationApplied: Bool
        public let cropNormalized: [Double]
    }
    public struct Timing: Codable {
        public let engineMs: Double
    }
    public struct Page: Codable {
        public let pageId: String, frameId: String, source: String, provenance: String, engine: String
        public let coordinateSystem: String
        public let capturedAtMonotonicMs: Int64
        public let accuracy: Accuracy
        public let recognitionLanguages: [String]
        public let usesLanguageCorrection: Bool
        public let coverage: Coverage
        public let image: ImageMetadata
        public let words: [Word]
        public let lines: [Line]
        public let warnings: [String]
        public let timing: Timing
    }

    /// Synchronous work: invoke off the UI/camera-delivery queue. Pass raw pixels with their
    /// ACTUAL EXIF orientation; pass .up if the owner has already oriented the pixels.
    /// Captured time must come from the owner's local monotonic clock, not Date or cloud time.
    public static func recognize(image: CGImage, orientation: CGImagePropertyOrientation,
                                 frameId: String, capturedAtMonotonicMs: Int64,
                                 accuracy: Accuracy = .fast, recognitionLanguages: [String] = [],
                                 coverage: Coverage = .unknown) throws -> Page {
        guard !frameId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              capturedAtMonotonicMs >= 0, image.width > 0, image.height > 0 else {
            throw InputError.invalidFrameMetadata
        }
        let swapsAxes: Bool
        switch orientation {
        case .left, .leftMirrored, .right, .rightMirrored: swapsAxes = true
        default: swapsAxes = false
        }
        let width = swapsAxes ? image.height : image.width
        let height = swapsAxes ? image.width : image.height
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = accuracy == .fast ? .fast : .accurate
        request.usesLanguageCorrection = false
        if !recognitionLanguages.isEmpty { request.recognitionLanguages = recognitionLanguages }
        let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])
        let started = ProcessInfo.processInfo.systemUptime
        try handler.perform([request])
        let engineMs = (ProcessInfo.processInfo.systemUptime - started) * 1_000
        let tokens = try NSRegularExpression(pattern: #"\S+"#)
        var lines: [Line] = [], words: [Word] = []
        var warnings = [
            "Only this image was processed; completeness of the menu/pages is unverified.",
            "Vision candidate confidence is line-level, not calibrated word/menu accuracy.",
            "Undetected or absent text regions cannot be inferred from OCR output alone."
        ]
        for (index, observation) in (request.results ?? []).enumerated() {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let text = candidate.string // Preserve exact recognized wording; no correction/trim.
            let lineId = "\(frameId)-line-\(index)"
            lines.append(Line(id: lineId, text: text, region: topLeft(observation.boundingBox),
                              candidateConfidence: candidate.confidence,
                              confidenceScope: "recognized-text-candidate-for-line"))
            // Whitespace tokenization preserves $12.50 and punctuation instead of splitting prices.
            let matches = tokens.matches(in: text, range: NSRange(text.startIndex..., in: text))
            for match in matches {
                guard let range = Range(match.range, in: text) else { continue }
                let rectangle: VNRectangleObservation?
                do { rectangle = try candidate.boundingBox(for: range) }
                catch { rectangle = nil }
                guard let rectangle, let region = topLeft(rectangle.boundingBox) else {
                    warnings.append("A token in \(lineId) could not be located; exact text remains in lines.")
                    continue
                }
                words.append(Word(id: "\(frameId)-w\(words.count)", text: String(text[range]),
                                  pageId: frameId, lineId: lineId, region: region,
                                  box: region.pixelBox(width, height), confidence: nil,
                                  lineCandidateConfidence: candidate.confidence))
            }
        }
        if lines.isEmpty {
            warnings.append("No text candidates were reported; this does not prove the image contains no text.")
        }
        return Page(pageId: frameId, frameId: frameId, source: "OWNER_PROVIDED_CGIMAGE",
                    provenance: "NATIVE_VISION_OCR", engine: "Apple Vision VNRecognizeTextRequest",
                    coordinateSystem: "normalizedImageTopLeft", capturedAtMonotonicMs: capturedAtMonotonicMs,
                    accuracy: accuracy, recognitionLanguages: request.recognitionLanguages,
                    usesLanguageCorrection: false, coverage: coverage,
                    image: ImageMetadata(width: width, height: height, originalSize: [image.width, image.height],
                                         exifOrientation: orientation.rawValue, orientationApplied: true,
                                         cropNormalized: [0, 0, 1, 1]),
                    words: words, lines: lines, warnings: warnings, timing: Timing(engineMs: engineMs))
    }

    /// Vision uses normalized bottom-left boxes. Convert once after orientation handling.
    /// Invalid boxes remain unlocated instead of being clamped/repaired into a guessed position.
    private static func topLeft(_ box: CGRect) -> Region? {
        let x = Double(box.minX), y = 1 - Double(box.maxY)
        let w = Double(box.width), h = Double(box.height)
        guard [x, y, w, h].allSatisfy(\.isFinite), x >= 0, y >= 0, w > 0, h > 0,
              x + w <= 1, y + h <= 1 else { return nil }
        return Region(x: x, y: y, width: w, height: h)
    }
}
