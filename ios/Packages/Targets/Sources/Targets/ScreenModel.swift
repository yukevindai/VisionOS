import Foundation

// R3 interpretation of one camera frame of a physical or digital interface.
// JSON keys follow the shared schema v3 `ScreenModel` (frameId, controls[{id,label,price?,region}],
// sections[], isPaymentScreen). Every other field is an R3-local addition proposed in README.md; it is
// not part of the shared contract until the owners agree.

/// Decodes an unrecognised raw value to a fallback instead of failing the whole model.
public protocol FallbackDecodable: RawRepresentable, Codable where RawValue == String {
    static var fallback: Self { get }
}
extension FallbackDecodable {
    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? Self.fallback
    }
}

public enum PanelType: String, FallbackDecodable, Sendable {
    case elevatorPanel, orderingKiosk, atmScreen, controlPanel, wallMenu, other, unknown
    public static let fallback = PanelType.unknown
}

public enum ElementType: String, FallbackDecodable, Sendable {
    case button, screenControl, menuItem, key, toggle, other
    public static let fallback = ElementType.other
}

/// Who produced the observation. Mock data must use `SIMULATED`.
public enum EvidenceSource: String, FallbackDecodable, Sendable {
    case simulated = "SIMULATED", ocr, visionModel, onDeviceDetector, manualAnnotation, unknown
    public static let fallback = EvidenceSource.unknown
}

/// Only the shared convention is supported: normalized 0...1, origin top-left, after orientation correction.
public enum CoordinateSystem: String, FallbackDecodable, Sendable {
    case normalizedImageTopLeft, unsupported
    public static let fallback = CoordinateSystem.unsupported
}

public enum Availability: String, FallbackDecodable, Sendable {
    /// The whole interface is in view and readable.
    case available
    /// Some of the interface is cut off, occluded or unreadable; absent controls may exist.
    case partial
    /// No usable interface was read from this frame.
    case unavailable
    public static let fallback = Availability.unavailable
}

/// How readable the provider judged a label. Unknown values decode as `partial`, never `clear`.
public enum Legibility: String, FallbackDecodable, Sendable {
    case clear, partial, unreadable
    public static let fallback = Legibility.partial
}

/// The upright image that `region` coordinates refer to.
public struct ImageInfo: Codable, Equatable, Sendable {
    public let widthPx: Int
    public let heightPx: Int
    /// EXIF orientation of the uploaded file (1 = already upright).
    public let exifOrientation: Int
    /// True when that orientation was applied before detection, so boxes are in upright coordinates.
    public let orientationApplied: Bool
    public init(widthPx: Int, heightPx: Int, exifOrientation: Int = 1, orientationApplied: Bool = true) {
        self.widthPx = widthPx; self.heightPx = heightPx
        self.exifOrientation = exifOrientation; self.orientationApplied = orientationApplied
    }
}

/// What the provider actually reported. Absent fields were not reported; none are estimated.
public struct ProviderEvidence: Codable, Equatable, Sendable {
    public let provider: String
    public let model: String
    /// False for any reply that did not come from the live provider API.
    public let live: Bool
    public let latencyMs: Double?
    public let modelVersion: String?
    public let responseId: String?
    public init(provider: String, model: String, live: Bool, latencyMs: Double? = nil,
                modelVersion: String? = nil, responseId: String? = nil) {
        self.provider = provider; self.model = model; self.live = live; self.latencyMs = latencyMs
        self.modelVersion = modelVersion; self.responseId = responseId
    }
}

public enum UnavailableReason: String, FallbackDecodable, Sendable {
    case notInView, unreadable, occluded, providerUnavailable, unknown
    public static let fallback = UnavailableReason.unknown
}

public struct BoundingBox: Codable, Equatable, Sendable {
    public let x: Double, y: Double, width: Double, height: Double
    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x; self.y = y; self.width = width; self.height = height
    }
    /// Same rule as R2's `ImageRegion.isValid`: finite, non-empty and inside the normalized frame.
    public var isValid: Bool {
        [x, y, width, height].allSatisfy(\.isFinite) && x >= 0 && y >= 0 && width > 0 && height > 0
            && x + width <= 1 && y + height <= 1
    }
}

/// An explicit interpretation supplied by the producer; missing/unknown never authorizes guidance.
public enum ControlSafety: String, FallbackDecodable, Sendable {
    case ordinary, emergency, payment, purchaseAuthorization, unknownCheckout, unknown
    public static let fallback = ControlSafety.unknown
}

public struct InteractiveElement: Codable, Equatable, Sendable {
    public let id: String
    /// Text as read from the interface. Empty when the label could not be read.
    public let label: String
    public let type: ElementType
    /// Nil when the element was detected but could not be located.
    public let region: BoundingBox?
    /// 0...1 when the source reports one.
    public let confidence: Double?
    public let price: String?
    public let section: String?
    /// Nil when the source does not judge legibility.
    public let legibility: Legibility?
    public let safety: ControlSafety?
    public init(id: String, label: String, type: ElementType, region: BoundingBox?, confidence: Double? = nil,
                price: String? = nil, section: String? = nil, legibility: Legibility? = nil, safety: ControlSafety? = nil) {
        self.id = id; self.label = label; self.type = type; self.region = region
        self.confidence = confidence; self.price = price; self.section = section; self.legibility = legibility; self.safety = safety
    }
}

public struct ScreenModel: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let frameId: String
    /// UTC milliseconds when the frame was captured.
    public let capturedAtMs: Int64
    public let panelType: PanelType
    public let availability: Availability
    public let unavailableReason: UnavailableReason?
    public let coordinateSystem: CoordinateSystem
    public let source: EvidenceSource
    public let controls: [InteractiveElement]
    public let sections: [String]
    public let isPaymentScreen: Bool
    public let notes: String?
    public let image: ImageInfo?
    public let evidence: ProviderEvidence?
    public init(schemaVersion: Int = 3, frameId: String, capturedAtMs: Int64, panelType: PanelType,
                availability: Availability = .available, unavailableReason: UnavailableReason? = nil,
                coordinateSystem: CoordinateSystem = .normalizedImageTopLeft, source: EvidenceSource,
                controls: [InteractiveElement], sections: [String] = [], isPaymentScreen: Bool = false,
                notes: String? = nil, image: ImageInfo? = nil, evidence: ProviderEvidence? = nil) {
        self.schemaVersion = schemaVersion; self.frameId = frameId; self.capturedAtMs = capturedAtMs
        self.panelType = panelType; self.availability = availability; self.unavailableReason = unavailableReason
        self.coordinateSystem = coordinateSystem; self.source = source; self.controls = controls
        self.sections = sections; self.isPaymentScreen = isPaymentScreen; self.notes = notes
        self.image = image; self.evidence = evidence
    }

    public var isSimulated: Bool { source == .simulated }

    /// A copy with different controls, for building variants of a frame.
    public func replacingControls(_ controls: [InteractiveElement]) -> ScreenModel {
        ScreenModel(schemaVersion: schemaVersion, frameId: frameId, capturedAtMs: capturedAtMs, panelType: panelType,
                    availability: availability, unavailableReason: unavailableReason,
                    coordinateSystem: coordinateSystem, source: source, controls: controls, sections: sections,
                    isPaymentScreen: isPaymentScreen, notes: notes, image: image, evidence: evidence)
    }
}
