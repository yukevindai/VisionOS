import Foundation
import XCTest
@testable import Guidance

final class GuidanceTests: XCTestCase {
    struct Fixture: Decodable {
        struct Policy: Decodable { let target, finger, delta: [Double]; let distance: Double; let direction: String; let expiryMs: Int64 }
        struct Measurement: Decodable { let xyz: [Double]; let sigma: Double; let support: Int }
        struct Geometry: Decodable {
            let width, height, imageWidth, imageHeight: Int
            let values: [Float]; let confidence: [UInt8]; let intrinsics, point, box: [Double]
            let depth, fingertip, surface: Measurement
        }
        struct Transforms: Decodable { let point, pose, world, camera, projected, backprojected: [Double] }
        let policy: [Policy]; let geometry: Geometry; let transforms: Transforms
    }
    private func fixture() throws -> Fixture {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "reference", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
    }
    private func v(_ a: [Double]) -> GuideVector3 { .init(x: a[0], y: a[1], z: a[2]) }
    private func m(_ x: Double = 0, _ y: Double = 0, _ z: Double = 1,
                   source: GuideMeasurementSource = .measuredDepth) -> GuideMeasurement {
        .init(xyz: .init(x: x, y: y, z: z), sigma: 0.02, support: 9, source: source)
    }
    private func assertVector(_ actual: GuideVector3?, _ expected: [Double], file: StaticString = #filePath, line: UInt = #line) throws {
        let actual = try XCTUnwrap(actual, file: file, line: line)
        for (a, b) in zip(actual.components, expected) { XCTAssertEqual(a, b, accuracy: 1e-6, file: file, line: line) }
    }
    func testPolicySignsDistanceTieBreakingAndExpiryMatchPython() throws {
        for item in try fixture().policy {
            var controller = GuideController()
            let cue = controller.update(target: .init(xyz: v(item.target), sigma: 0.02, support: 9, source: .measuredDepth),
                finger: .init(xyz: v(item.finger), sigma: 0.02, support: 9, source: .measuredDepth),
                targetState: .visible, observedAtMs: 900, nowMs: 1_000, generation: 1, evidenceGeneration: 1)
            XCTAssertEqual(cue.direction.rawValue, item.direction)
            XCTAssertEqual(try XCTUnwrap(cue.distance), item.distance, accuracy: 1e-8)
            try assertVector(cue.delta, item.delta)
            XCTAssertEqual(cue.expiresAtMs, item.expiryMs)
        }
    }
    func testDepthFingertipAndSurfaceMatchPython() throws {
        let f = try fixture().geometry
        let plane = GuideDepthPlane(width: f.width, height: f.height, imageWidth: f.imageWidth,
                                    imageHeight: f.imageHeight, values: f.values, confidence: f.confidence)
        let k = GuideIntrinsics(fx: f.intrinsics[0], fy: f.intrinsics[1], cx: f.intrinsics[2], cy: f.intrinsics[3])
        let point = GuidePoint2(x: f.point[0], y: f.point[1])
        let actual = [GuideGeometry.depth(at: point, depth: plane, intrinsics: k),
                      GuideGeometry.fingertip(at: point, depth: plane, intrinsics: k),
                      GuideGeometry.surface(box: .init(xMin: f.box[0], yMin: f.box[1], xMax: f.box[2], yMax: f.box[3]), depth: plane, intrinsics: k)]
        for (measurement, expected) in zip(actual, [f.depth, f.fingertip, f.surface]) {
            let measurement = try XCTUnwrap(measurement)
            try assertVector(measurement.xyz, expected.xyz)
            XCTAssertEqual(measurement.sigma, expected.sigma, accuracy: 1e-8)
            XCTAssertEqual(measurement.support, expected.support)
        }
    }
    func testProjectionAndCoordinateTransformsMatchPython() throws {
        let f = try fixture(), t = f.transforms
        let k = GuideIntrinsics(fx: f.geometry.intrinsics[0], fy: f.geometry.intrinsics[1], cx: f.geometry.intrinsics[2], cy: f.geometry.intrinsics[3])
        try assertVector(GuideGeometry.world(v(t.point), pose: t.pose), t.world)
        try assertVector(GuideGeometry.camera(v(t.world), pose: t.pose), t.camera)
        let projected = try XCTUnwrap(GuideGeometry.project(v(t.point), intrinsics: k))
        XCTAssertEqual(projected.x, t.projected[0], accuracy: 1e-8)
        XCTAssertEqual(projected.y, t.projected[1], accuracy: 1e-8)
        try assertVector(GuideGeometry.backproject(projected, z: t.point[2], intrinsics: k), t.backprojected)
    }
    func testUnknownConfidenceAndUnsupportedDepthCannotProduceMeasurements() {
        let k = GuideIntrinsics(fx: 10, fy: 10, cx: 5, cy: 5), point = GuidePoint2(x: 5, y: 5)
        for confidence in [nil, [UInt8](repeating: 0, count: 100), [UInt8](repeating: 2, count: 99)] {
            let plane = GuideDepthPlane(width: 10, height: 10, imageWidth: 10, imageHeight: 10,
                                        values: [Float](repeating: 1, count: 100), confidence: confidence)
            XCTAssertNil(GuideGeometry.depth(at: point, depth: plane, intrinsics: k))
            XCTAssertNil(GuideGeometry.fingertip(at: point, depth: plane, intrinsics: k))
            XCTAssertNil(GuideGeometry.surface(box: .init(xMin: 1, yMin: 1, xMax: 9, yMax: 9), depth: plane, intrinsics: k))
        }
    }
    func testDiscontinuousDepthAndUnconfidentCenterFailClosed() {
        let k = GuideIntrinsics(fx: 10, fy: 10, cx: 5, cy: 5), point = GuidePoint2(x: 5, y: 5)
        var values = [Float](repeating: 1, count: 100)
        values[55] = 0.5
        let plane = GuideDepthPlane(width: 10, height: 10, imageWidth: 10, imageHeight: 10,
                                    values: values, confidence: [UInt8](repeating: 2, count: 100))
        XCTAssertNil(GuideGeometry.depth(at: point, depth: plane, intrinsics: k))
        XCTAssertNil(GuideGeometry.fingertip(at: point, depth: plane, intrinsics: k))
    }
    func testInvalidCoordinatesAndPoseCannotTrapOrInventGeometry() {
        let k = GuideIntrinsics(fx: 10, fy: 10, cx: 5, cy: 5)
        XCTAssertNil(GuideGeometry.backproject(.init(x: .nan, y: 1), z: 1, intrinsics: k))
        XCTAssertNil(GuideGeometry.project(.init(x: 1, y: 1, z: 0.1), intrinsics: k))
        XCTAssertNil(GuideGeometry.camera(.init(x: 1, y: 1, z: 1), pose: []))
        XCTAssertNil(GuideGeometry.world(.init(x: 1, y: 1, z: 1), pose: [Double](repeating: 0, count: 16)))
    }
    func testStaleFutureHazardGenerationAndPredictionStop() {
        var controller = GuideController()
        let cases: [(Int64, Int64, GuideTrackingState, UInt64, Bool, GuideReason)] = [
            (0,300,.visible,1,false,.staleFrame), (301,300,.visible,1,false,.staleFrame),
            (100,100,.visible,2,false,.generationMismatch), (100,100,.predicted,1,false,.objectUnverified),
            (100,100,.lost,1,false,.objectUnverified), (100,100,.visible,1,true,.hazardStop)]
        for (observed, now, state, evidence, hazard, reason) in cases {
            let cue = controller.update(target: m(0.1), finger: m(), targetState: state,
                observedAtMs: observed, nowMs: now, generation: 1, evidenceGeneration: evidence, hazard: hazard)
            XCTAssertEqual(cue.direction, .stop); XCTAssertEqual(cue.reason, reason)
            XCTAssertFalse(cue.onTarget)
        }
        for source in [GuideMeasurementSource.estimated, .predicted] {
            let cue = controller.update(target: m(0.1, source: source), finger: m(), targetState: .tracked,
                observedAtMs: 100, nowMs: 100, generation: 1, evidenceGeneration: 1)
            XCTAssertEqual(cue.reason, .unmeasuredDepth)
        }
    }
    func testHysteresisRetainsAxisUntilNewAxisIs25PercentStronger() {
        var c = GuideController()
        func update(_ x: Double, _ y: Double) -> GuideCue {
            c.update(target: m(x,y), finger: m(), targetState: .visible, observedAtMs: 100,
                     nowMs: 100, generation: 1, evidenceGeneration: 1)
        }
        XCTAssertEqual(update(0.1,0.09).direction, .right)
        XCTAssertEqual(update(0.1,0.12).direction, .right)
        XCTAssertEqual(update(0.1,0.126).direction, .down)
    }
    func testExpiryUsesEarlierOf180msHoldAnd300msEvidenceDeadline() {
        var c = GuideController()
        let cue = c.update(target: m(0.1), finger: m(), targetState: .visible,
            observedAtMs: 100, nowMs: 350, generation: 1, evidenceGeneration: 1)
        XCTAssertEqual(cue.expiresAtMs, 400)
        XCTAssertEqual(c.current(nowMs: 399).direction, .right)
        XCTAssertEqual(c.current(nowMs: 400).reason, .expired)
        XCTAssertFalse(c.current(nowMs: 400).onTarget)
    }
    func testOnTargetRequiresDistinctFreshMeasuredDwellAndNeverCompletesTask() {
        var c = GuideController()
        func update(_ time: Int64) -> GuideCue {
            c.update(target: m(0.01), finger: m(), targetState: .visible,
                     observedAtMs: time, nowMs: time, generation: 1, evidenceGeneration: 1)
        }
        XCTAssertFalse(update(100).onTarget)
        XCTAssertFalse(update(200).onTarget)
        XCTAssertFalse(update(300).onTarget)
        let aligned = update(400)
        XCTAssertTrue(aligned.onTarget)
        XCTAssertEqual(aligned.direction, .stop)
        XCTAssertEqual(aligned.reason, .withinTolerance)
        _ = c.update(target: nil, finger: m(), targetState: .lost,
                     observedAtMs: 410, nowMs: 410, generation: 1, evidenceGeneration: 1)
        XCTAssertFalse(update(420).onTarget)
    }
    func testRepeatedFrameAndGenerationChangeCannotCompleteDwell() {
        var c = GuideController()
        for now in [100,200,300] {
            let cue = c.update(target: m(), finger: m(), targetState: .visible, observedAtMs: 100,
                               nowMs: Int64(now), generation: 1, evidenceGeneration: 1)
            XCTAssertFalse(cue.onTarget)
        }
        let cue = c.update(target: m(), finger: m(), targetState: .visible, observedAtMs: 400,
                           nowMs: 400, generation: 2, evidenceGeneration: 2)
        XCTAssertFalse(cue.onTarget)
    }
}
