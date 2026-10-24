import Foundation
import UIKit

/// R2 adapter proposal, not a replacement for the teammates' schema-v3 wire format.
struct CameraCapabilities: Equatable {
    let selectedLens: String
    let depthSupported: Bool
    let fallbackReason: String?
    let geometryID: String
}

struct FrameTransform: Equatable, Sendable {
    // Axis-aligned oriented crop/resize mapping back into the full oriented image.
    let originX: Double
    let originY: Double
    let scaleX: Double
    let scaleY: Double
    static let identity = FrameTransform(originX: 0, originY: 0, scaleX: 1, scaleY: 1)
    var isValid: Bool {
        [originX, originY, scaleX, scaleY].allSatisfy(\.isFinite) &&
        originX >= 0 && originY >= 0 && scaleX > 0 && scaleY > 0 &&
        originX + scaleX <= 1 && originY + scaleY <= 1
    }
    func originalPoint(x: Double, y: Double) -> CGPoint? {
        guard isValid, x.isFinite, y.isFinite, (0...1).contains(x), (0...1).contains(y) else { return nil }
        return CGPoint(x: originX + x * scaleX, y: originY + y * scaleY)
    }
}

struct FrameSnapshot {
    let context: SessionContext
    let frameID: String
    let capturedAtMs: Int64
    let expiresAtMs: Int64
    let geometryID: String
    let transform: FrameTransform
    let preview: UIImage?
    let encodedImage: Data?
    let trackingNormal: Bool
    let depthUsable: Bool
}

struct SharedAudioInput: Equatable, Sendable {
    // Opaque reference supplied by R1; R2 does not open another microphone engine.
    let identifier: String
}

struct CameraPolicy: Equatable {
    let widestCompatibleRearCamera = true
    let preserveFullFieldOfView = true
    let forbidAutomaticLensChangeDuringGuidance = true
}
