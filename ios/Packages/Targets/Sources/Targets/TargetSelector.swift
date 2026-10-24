import Foundation

// Matches a user's words to one control in a ScreenModel. The result is a proposal for R2's task engine:
// it carries no movement, guidance or haptic command, and selecting a target authorizes nothing.

public enum SelectionStatus: String, Codable, Sendable {
    case selected = "SELECTED", notFound = "NOT_FOUND", ambiguous = "AMBIGUOUS"
    /// The provider failed (HTTP error, timeout, missing key), so nothing is known about the target.
    /// Never treat this as the target being absent.
    case providerUnavailable = "PROVIDER_UNAVAILABLE"
}

public enum SelectionReason: String, Codable, Sendable {
    case providerUnavailable = "PROVIDER_UNAVAILABLE"
    // NOT_FOUND
    case emptyRequest = "EMPTY_REQUEST"
    case malformedModel = "MALFORMED_MODEL"
    case screenUnavailable = "SCREEN_UNAVAILABLE"
    case unsupportedCoordinates = "UNSUPPORTED_COORDINATES"
    case noMatch = "NO_MATCH"
    /// No match, but part of the interface was not read; asking the user to reposition may help.
    case noMatchPartialView = "NO_MATCH_PARTIAL_VIEW"
    /// The only matching control has no usable bounding box.
    case invalidRegion = "INVALID_REGION"
    // AMBIGUOUS
    case duplicateLabel = "DUPLICATE_LABEL"
    case multipleMatches = "MULTIPLE_MATCHES"
    case lowConfidence = "LOW_CONFIDENCE"
}

/// How the request matched the label. Earlier cases rank higher.
public enum MatchKind: String, Codable, Comparable, Sendable {
    case exact, labelInRequest, requestInLabel
    private var rank: Int {
        switch self {
        case .exact: return 0
        case .labelInRequest: return 1
        case .requestInLabel: return 2
        }
    }
    public static func < (lhs: MatchKind, rhs: MatchKind) -> Bool { lhs.rank < rhs.rank }
}

/// Field names follow the shared `Target` record where one exists (targetId, label, imageRegion, frameId).
public struct SelectedTarget: Codable, Equatable, Sendable {
    public let targetId: String
    public let label: String
    public let elementType: ElementType
    public let imageRegion: BoundingBox
    public let coordinateSystem: CoordinateSystem
    public let frameId: String
    public let capturedAtMs: Int64
    public let panelType: PanelType
    public let confidence: Double?
    public let source: EvidenceSource
    public let match: MatchKind
}

public struct TargetSelection: Codable, Equatable, Sendable {
    public let status: SelectionStatus
    public let request: String
    public let frameId: String
    public let source: EvidenceSource
    public let target: SelectedTarget?
    public let reason: SelectionReason?
    /// Matching control IDs, in model order. Empty when selected or when nothing matched.
    public let candidateIds: [String]
    /// Shared schema v3 error code, where one applies.
    public let errorCode: String?

    static func selected(_ request: String, _ model: ScreenModel, _ target: SelectedTarget) -> TargetSelection {
        TargetSelection(status: .selected, request: request, frameId: model.frameId, source: model.source,
                        target: target, reason: nil, candidateIds: [], errorCode: nil)
    }
    static func notFound(_ request: String, _ model: ScreenModel, _ reason: SelectionReason,
                         candidates: [String] = []) -> TargetSelection {
        TargetSelection(status: .notFound, request: request, frameId: model.frameId, source: model.source,
                        target: nil, reason: reason, candidateIds: candidates,
                        errorCode: reason == .invalidRegion ? "LOW_CONFIDENCE" : nil)
    }
    static func ambiguous(_ request: String, _ model: ScreenModel, _ reason: SelectionReason,
                          candidates: [String]) -> TargetSelection {
        TargetSelection(status: .ambiguous, request: request, frameId: model.frameId, source: model.source,
                        target: nil, reason: reason, candidateIds: candidates,
                        errorCode: reason == .lowConfidence ? "LOW_CONFIDENCE" : "LABEL_AMBIGUOUS")
    }
}

public struct TargetSelector: Sendable {
    /// Below this, a single match is reported as AMBIGUOUS rather than selected. Nil confidence is accepted.
    public var minimumConfidence: Double
    public init(minimumConfidence: Double = 0.6) { self.minimumConfidence = minimumConfidence }

    public func select(_ request: String, in model: ScreenModel) -> TargetSelection {
        let wanted = TextMatcher.contentTokens(request)
        guard !wanted.isEmpty else { return .notFound(request, model, .emptyRequest) }
        let ids = model.controls.map(\.id)
        guard !model.frameId.isEmpty, !ids.contains(""), Set(ids).count == ids.count else {
            return .notFound(request, model, .malformedModel)
        }
        guard model.availability != .unavailable else {
            if model.unavailableReason == .providerUnavailable {
                return TargetSelection(status: .providerUnavailable, request: request, frameId: model.frameId,
                                       source: model.source, target: nil, reason: .providerUnavailable,
                                       candidateIds: [], errorCode: nil)
            }
            return .notFound(request, model, .screenUnavailable)
        }
        guard model.coordinateSystem == .normalizedImageTopLeft else {
            return .notFound(request, model, .unsupportedCoordinates)
        }

        let matches = model.controls.compactMap { element in
            TextMatcher.match(request: wanted, label: element.label).map { (element, $0) }
        }
        guard let best = matches.map(\.1).min() else {
            return .notFound(request, model, model.availability == .partial ? .noMatchPartialView : .noMatch)
        }
        // Every control in the best tier counts, including ones without a usable region or with low
        // confidence, so an unlocatable twin can never be silently dropped in favour of the other.
        let tier = matches.filter { $0.1 == best }.map(\.0)
        // A typed control outranks an untyped element with the same text (e.g. a button and its printed plate).
        let typed = tier.filter { $0.type != .other }
        let top = typed.isEmpty ? tier : typed
        let candidates = top.map(\.id)
        if top.count > 1 {
            let labels = Set(top.map { TextMatcher.tokens($0.label) })
            return .ambiguous(request, model, labels.count == 1 ? .duplicateLabel : .multipleMatches,
                              candidates: candidates)
        }
        let element = top[0]
        guard let region = element.region, region.isValid else {
            return .notFound(request, model, .invalidRegion, candidates: candidates)
        }
        if let confidence = element.confidence, !(confidence >= minimumConfidence) {
            return .ambiguous(request, model, .lowConfidence, candidates: candidates)
        }
        if let legibility = element.legibility, legibility != .clear {
            return .ambiguous(request, model, .lowConfidence, candidates: candidates)
        }
        return .selected(request, model, SelectedTarget(
            targetId: element.id, label: element.label, elementType: element.type, imageRegion: region,
            coordinateSystem: model.coordinateSystem, frameId: model.frameId, capturedAtMs: model.capturedAtMs,
            panelType: model.panelType, confidence: element.confidence, source: model.source, match: best))
    }
}

/// Device-independent word matching. No layouts, brands or positions are known here.
enum TextMatcher {
    /// Request words that never identify a control.
    static let fillerWords: Set<String> = [
        "a", "an", "the", "please", "can", "could", "you", "i", "me", "my", "want", "would", "like", "need",
        "to", "for", "find", "locate", "where", "is", "show", "help", "press", "push", "tap", "touch", "hit",
        "select", "choose", "pick", "get", "order", "go", "take", "button", "item", "option", "key", "control"
    ]
    static let numberWords: [String: String] = [
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
        "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
        "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
        "nineteen": "19", "twenty": "20", "first": "1", "second": "2", "third": "3", "fourth": "4",
        "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10"
    ]

    /// Lower-cased, diacritic-folded alphanumeric words; number words and ordinals become digits.
    static func tokens(_ text: String) -> [String] {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil).lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
            .map(normalizeNumber)
    }

    static func contentTokens(_ text: String) -> [String] { tokens(text).filter { !fillerWords.contains($0) } }

    static func match(request wanted: [String], label: String) -> MatchKind? {
        let all = tokens(label)
        guard !all.isEmpty else { return nil }
        let content = all.filter { !fillerWords.contains($0) }
        let labelWords = Set(content.isEmpty ? all : content)
        let requestWords = Set(wanted)
        if labelWords == requestWords { return .exact }
        // Numbers identify controls (floors, keypad digits), so every number asked for must be on the label.
        let requestNumbers = requestWords.filter(isNumber)
        if labelWords.isSubset(of: requestWords), requestNumbers.isSubset(of: labelWords) { return .labelInRequest }
        if requestWords.isSubset(of: labelWords) { return .requestInLabel }
        return nil
    }

    static func isNumber(_ word: String) -> Bool { !word.isEmpty && word.allSatisfy(\.isNumber) }

    private static func normalizeNumber(_ word: String) -> String {
        if let digits = numberWords[word] { return digits }
        for suffix in ["st", "nd", "rd", "th"] where word.hasSuffix(suffix) {
            let stem = String(word.dropLast(2))
            if isNumber(stem) { return stem }
        }
        return word
    }
}
