import Foundation

public struct GuidePoint2: Equatable, Sendable {
    public let x: Double, y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

/// Optical camera coordinates: right, down, forward, in metres.
public struct GuideVector3: Equatable, Sendable {
    public let x: Double, y: Double, z: Double
    public init(x: Double, y: Double, z: Double) { self.x = x; self.y = y; self.z = z }
    public var isFinite: Bool { x.isFinite && y.isFinite && z.isFinite }
    public var length: Double { sqrt(x * x + y * y + z * z) }
    public var components: [Double] { [x, y, z] }
}

public struct GuideIntrinsics: Equatable, Sendable {
    public let fx: Double, fy: Double, cx: Double, cy: Double
    public init(fx: Double, fy: Double, cx: Double, cy: Double) {
        self.fx = fx; self.fy = fy; self.cx = cx; self.cy = cy
    }
    var isValid: Bool { [fx, fy, cx, cy].allSatisfy(\.isFinite) && fx > 0 && fy > 0 }
}

public struct GuideBox2: Equatable, Sendable {
    public let xMin: Double, yMin: Double, xMax: Double, yMax: Double
    public init(xMin: Double, yMin: Double, xMax: Double, yMax: Double) {
        self.xMin = xMin; self.yMin = yMin; self.xMax = xMax; self.yMax = yMax
    }
}

public struct GuideDepthPlane: Sendable {
    public let width: Int, height: Int, imageWidth: Int, imageHeight: Int
    public let values: [Float]
    public let confidence: [UInt8]?
    public init(width: Int, height: Int, imageWidth: Int, imageHeight: Int,
                values: [Float], confidence: [UInt8]?) {
        self.width = width; self.height = height; self.imageWidth = imageWidth; self.imageHeight = imageHeight
        self.values = values; self.confidence = confidence
    }
    var isValid: Bool {
        let size = width.multipliedReportingOverflow(by: height)
        return width > 0 && height > 0 && imageWidth > 0 && imageHeight > 0 &&
            !size.overflow && values.count == size.partialValue && confidence?.count == values.count
    }
    func confident(_ index: Int) -> Bool {
        guard let confidence else { return false }
        return (1...2).contains(confidence[index])
    }
}

public enum GuideMeasurementSource: String, Sendable { case measuredDepth, estimated, predicted }
public struct GuideMeasurement: Equatable, Sendable {
    public let xyz: GuideVector3
    public let sigma: Double
    public let support: Int
    public let source: GuideMeasurementSource
    public init(xyz: GuideVector3, sigma: Double, support: Int, source: GuideMeasurementSource) {
        self.xyz = xyz; self.sigma = sigma; self.support = support; self.source = source
    }
}

public enum GuideGeometry {
    public static func backproject(_ point: GuidePoint2, z: Double, intrinsics: GuideIntrinsics) -> GuideVector3? {
        guard point.x.isFinite, point.y.isFinite, z.isFinite, z > 0, intrinsics.isValid else { return nil }
        let result = GuideVector3(x: (point.x - intrinsics.cx) / intrinsics.fx * z,
                                 y: (point.y - intrinsics.cy) / intrinsics.fy * z, z: z)
        return result.isFinite ? result : nil
    }
    public static func project(_ point: GuideVector3, intrinsics: GuideIntrinsics) -> GuidePoint2? {
        guard point.isFinite, point.z > 0.1, intrinsics.isValid else { return nil }
        let result = GuidePoint2(x: intrinsics.fx * point.x / point.z + intrinsics.cx,
                                 y: intrinsics.fy * point.y / point.z + intrinsics.cy)
        return result.x.isFinite && result.y.isFinite ? result : nil
    }

    public static func depth(at point: GuidePoint2, depth: GuideDepthPlane, intrinsics: GuideIntrinsics,
                             radius: Int = 1) -> GuideMeasurement? {
        guard radius >= 1, radius <= 8, let (x, y) = location(point, depth),
              x >= radius, y >= radius, x < depth.width - radius, y < depth.height - radius else { return nil }
        var values: [Double] = []
        for row in (y-radius)...(y+radius) {
            for col in (x-radius)...(x+radius) {
                let index = row * depth.width + col, z = Double(depth.values[row * depth.width + col])
                if depth.confident(index), z.isFinite, z > 0.12, z < 4 { values.append(z) }
            }
        }
        let patchCount = (2 * radius + 1) * (2 * radius + 1)
        guard values.count >= max(5, Int(Double(patchCount) * 0.65)),
              let min = values.min(), let max = values.max(), max - min <= 0.04 else { return nil }
        let z = median(values)
        guard let xyz = backproject(point, z: z, intrinsics: intrinsics) else { return nil }
        return GuideMeasurement(xyz: xyz, sigma: Swift.max(0.012, 1.4826 * median(values.map { abs($0-z) })),
                                support: values.count, source: .measuredDepth)
    }

    public static func fingertip(at point: GuidePoint2, depth: GuideDepthPlane,
                                 intrinsics: GuideIntrinsics) -> GuideMeasurement? {
        guard let (x, y) = location(point, depth), x >= 1, y >= 1,
              x < depth.width - 1, y < depth.height - 1 else { return nil }
        let centerIndex = y * depth.width + x
        let center = Double(depth.values[centerIndex])
        guard center.isFinite, center > 0.12, center < 4, depth.confident(centerIndex) else { return nil }
        var values: [Double] = []
        for row in (y-1)...(y+1) {
            for col in (x-1)...(x+1) {
                let index = row * depth.width + col, z = Double(depth.values[row * depth.width + col])
                if depth.confident(index), z.isFinite, abs(z-center) < 0.018 { values.append(z) }
            }
        }
        guard values.count >= 3, let xyz = backproject(point, z: median(values), intrinsics: intrinsics) else { return nil }
        return GuideMeasurement(xyz: xyz, sigma: 0.02, support: values.count, source: .measuredDepth)
    }

    public static func surface(box: GuideBox2, depth: GuideDepthPlane,
                               intrinsics: GuideIntrinsics) -> GuideMeasurement? {
        guard depth.isValid, intrinsics.isValid,
              [box.xMin, box.yMin, box.xMax, box.yMax].allSatisfy(\.isFinite),
              box.xMin >= 0, box.yMin >= 0, box.xMax > box.xMin, box.yMax > box.yMin,
              box.xMax <= Double(depth.imageWidth), box.yMax <= Double(depth.imageHeight) else { return nil }
        let center = GuidePoint2(x: (box.xMin + box.xMax) / 2, y: (box.yMin + box.yMax) / 2)
        let sx = Double(depth.width) / Double(depth.imageWidth)
        let sy = Double(depth.height) / Double(depth.imageHeight)
        for (divisor, share, least) in [(6.0, 0.12, 5.0), (2.2, 0.10, 40.0)] {
            let halfX = max(2, (box.xMax-box.xMin)/divisor), halfY = max(2, (box.yMax-box.yMin)/divisor)
            let left = max(0, Int(floor((center.x-halfX)*sx)))
            let top = max(0, Int(floor((center.y-halfY)*sy)))
            let right = min(depth.width, Int(floor((center.x+halfX+1)*sx)))
            let bottom = min(depth.height, Int(floor((center.y+halfY+1)*sy)))
            guard right > left, bottom > top else { continue }
            var values: [Double] = []
            for row in top..<bottom {
                for col in left..<right {
                    let index = row * depth.width + col, z = Double(depth.values[row * depth.width + col])
                    if depth.confident(index), z.isFinite, z > 0.15, z < 4 { values.append(z) }
                }
            }
            let required = divisor == 6 ? max(5, 0.3 * Double((right-left)*(bottom-top))) : 30
            guard Double(values.count) >= required else { continue }
            let sorted = values.sorted()
            var start = 0, end = 0, bestCount = -1
            var bestLevel = sorted[0], selectedLevel: Double?
            while start < sorted.count {
                let level = sorted[start], upper = level + max(0.03, 0.06 * level)
                while end < sorted.count && sorted[end] <= upper { end += 1 }
                let count = end - start
                if count > bestCount { bestLevel = level; bestCount = count }
                if selectedLevel == nil, Double(count) >= max(least, share * Double(values.count)) {
                    selectedLevel = level
                }
                repeat { start += 1 } while start < sorted.count && sorted[start] == level
            }
            let level = selectedLevel ?? bestLevel
            let upper = level + max(0.03, 0.06 * level)
            let cluster = values.filter { $0 >= level && $0 <= upper }
            guard let xyz = backproject(center, z: median(cluster), intrinsics: intrinsics) else { return nil }
            return GuideMeasurement(xyz: xyz, sigma: 0.025, support: cluster.count, source: .measuredDepth)
        }
        return nil
    }

    /// Row-major ARKit camera-to-world rigid transform. ARKit's y-up/z-back axes are explicit.
    public static func world(_ point: GuideVector3, pose: [Double]) -> GuideVector3? {
        guard point.isFinite, validPose(pose) else { return nil }
        let p = [point.x, -point.y, -point.z]
        var result = [Double](repeating: 0, count: 3)
        for row in 0..<3 {
            result[row] = pose[row * 4 + 3]
            for col in 0..<3 { result[row] += pose[row * 4 + col] * p[col] }
        }
        return GuideVector3(x: result[0], y: result[1], z: result[2])
    }
    public static func camera(_ point: GuideVector3, pose: [Double]) -> GuideVector3? {
        guard point.isFinite, validPose(pose) else { return nil }
        let p = [point.x-pose[3], point.y-pose[7], point.z-pose[11]]
        var result = [Double](repeating: 0, count: 3)
        for col in 0..<3 {
            for row in 0..<3 { result[col] += pose[row * 4 + col] * p[row] }
        }
        return GuideVector3(x: result[0], y: -result[1], z: -result[2])
    }
    private static func validPose(_ pose: [Double]) -> Bool {
        guard pose.count == 16, pose.allSatisfy(\.isFinite),
              abs(pose[12]) < 1e-6, abs(pose[13]) < 1e-6, abs(pose[14]) < 1e-6, abs(pose[15]-1) < 1e-6 else { return false }
        for a in 0..<3 { for b in 0..<3 {
            let dot = (0..<3).reduce(0.0) { $0 + pose[$1*4+a]*pose[$1*4+b] }
            if abs(dot - (a == b ? 1 : 0)) > 1e-4 { return false }
        } }
        return true
    }
    private static func location(_ point: GuidePoint2, _ depth: GuideDepthPlane) -> (Int, Int)? {
        guard depth.isValid, point.x.isFinite, point.y.isFinite,
              point.x >= 0, point.y >= 0, point.x < Double(depth.imageWidth), point.y < Double(depth.imageHeight) else { return nil }
        return (Int(floor(point.x * Double(depth.width) / Double(depth.imageWidth))),
                Int(floor(point.y * Double(depth.height) / Double(depth.imageHeight))))
    }
    private static func median(_ values: [Double]) -> Double {
        let sorted = values.sorted(), center = values.count / 2
        return values.count % 2 == 1 ? sorted[center] : (sorted[center-1]+sorted[center])/2
    }
}
