import CoreGraphics
import Guidance
import ImageIO

/// Vision and the uploaded JPEG use upright coordinates; ARKit depth uses the raw sensor image.
enum NativePerceptionGeometry {
    static func imageOrientation(_ rotation: NativeMediaVideoRotation) -> CGImagePropertyOrientation {
        switch rotation {
        case .degrees0: return .up
        case .degrees90: return .right
        case .degrees180: return .down
        case .degrees270: return .left
        }
    }

    static func rawPoint(_ point: CGPoint, rotation: NativeMediaVideoRotation) -> CGPoint {
        switch rotation {
        case .degrees0: return point
        case .degrees90: return CGPoint(x: point.y, y: 1 - point.x)
        case .degrees180: return CGPoint(x: 1 - point.x, y: 1 - point.y)
        case .degrees270: return CGPoint(x: 1 - point.y, y: point.x)
        }
    }

    static func rawBox(_ box: CGRect, rotation: NativeMediaVideoRotation, width: Int, height: Int) -> GuideBox2 {
        let corners = [CGPoint(x: box.minX, y: box.minY), CGPoint(x: box.maxX, y: box.minY),
                       CGPoint(x: box.minX, y: box.maxY), CGPoint(x: box.maxX, y: box.maxY)]
            .map { rawPoint($0, rotation: rotation) }
        return GuideBox2(xMin: Double(corners.map(\.x).min()!) * Double(width),
                         yMin: Double(corners.map(\.y).min()!) * Double(height),
                         xMax: Double(corners.map(\.x).max()!) * Double(width),
                         yMax: Double(corners.map(\.y).max()!) * Double(height))
    }

    static func upright(_ measurement: GuideMeasurement?, rotation: NativeMediaVideoRotation) -> GuideMeasurement? {
        guard let measurement else { return nil }
        let p = measurement.xyz
        let xyz: GuideVector3
        switch rotation {
        case .degrees0: xyz = p
        case .degrees90: xyz = GuideVector3(x: -p.y, y: p.x, z: p.z)
        case .degrees180: xyz = GuideVector3(x: -p.x, y: -p.y, z: p.z)
        case .degrees270: xyz = GuideVector3(x: p.y, y: -p.x, z: p.z)
        }
        return GuideMeasurement(xyz: xyz, sigma: measurement.sigma, support: measurement.support, source: measurement.source)
    }

    /// A tracker must not jump to a differently sized object between observations.
    static func validTrackedTransition(from previous: CGRect, to current: CGRect) -> Bool {
        guard previous.width > 0, previous.height > 0, current.width > 0, current.height > 0 else { return false }
        let areaRatio = current.width * current.height / (previous.width * previous.height)
        let dx = current.midX - previous.midX, dy = current.midY - previous.midY
        return areaRatio.isFinite && (0.6...1.67).contains(areaRatio) && hypot(dx, dy) <= 0.15
    }

    /// At least 25% hand overlap makes the target surface unobserved. Stop rather than
    /// sample foreground hand depth as if it belonged to the target.
    static func handOccludes(target: CGRect, hands: [CGRect]) -> Bool {
        let area = target.width * target.height
        guard area.isFinite, area > 0 else { return true }
        return hands.contains { hand in
            let overlap = target.intersection(hand)
            return !overlap.isNull && overlap.width * overlap.height / area >= 0.25
        }
    }

    static func validTrackedBox(_ box: CGRect, confidence: Float) -> Bool {
        [box.minX, box.minY, box.width, box.height].allSatisfy(\.isFinite) &&
        confidence.isFinite && confidence >= 0.7 && box.minX >= 0 && box.minY >= 0 &&
        box.width > 0 && box.height > 0 && box.maxX <= 1 && box.maxY <= 1
    }
}
