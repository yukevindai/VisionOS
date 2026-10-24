import CoreGraphics
import AVFoundation
import Guidance
import XCTest
@testable import Reflex

final class NativePerceptionGeometryTests: XCTestCase {
    func testUprightImagePointMapsBackToRawSensorForEveryRotation() {
        let point = CGPoint(x: 0.2, y: 0.7)
        let cases: [(NativeMediaVideoRotation, CGPoint)] = [
            (.degrees0, CGPoint(x: 0.2, y: 0.7)), (.degrees90, CGPoint(x: 0.7, y: 0.8)),
            (.degrees180, CGPoint(x: 0.8, y: 0.3)), (.degrees270, CGPoint(x: 0.3, y: 0.2))
        ]
        for (rotation, expected) in cases {
            let raw = NativePerceptionGeometry.rawPoint(point, rotation: rotation)
            XCTAssertEqual(raw.x, expected.x, accuracy: 0.0001)
            XCTAssertEqual(raw.y, expected.y, accuracy: 0.0001)
        }
    }

    func testDepthVectorAndImagePointUseConsistentRotation() {
        let raw = GuideMeasurement(xyz: .init(x: 0.1, y: 0.2, z: 1), sigma: 0.02,
                                   support: 8, source: .measuredDepth)
        let cases: [(NativeMediaVideoRotation, Double, Double)] = [
            (.degrees0, 0.1, 0.2), (.degrees90, -0.2, 0.1),
            (.degrees180, -0.1, -0.2), (.degrees270, 0.2, -0.1)
        ]
        for (rotation, x, y) in cases {
            let rotated = NativePerceptionGeometry.upright(raw, rotation: rotation)
            XCTAssertEqual(rotated?.xyz, .init(x: x, y: y, z: 1))
            XCTAssertEqual(rotated?.source, .measuredDepth)
            XCTAssertEqual(rotated?.support, 8)
        }
    }

    func testBoxCoversAllFourTransformedCorners() {
        let box = NativePerceptionGeometry.rawBox(CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4),
                                                  rotation: .degrees90, width: 1_000, height: 500)
        XCTAssertEqual(box.xMin, 200, accuracy: 0.001)
        XCTAssertEqual(box.xMax, 600, accuracy: 0.001)
        XCTAssertEqual(box.yMin, 300, accuracy: 0.001)
        XCTAssertEqual(box.yMax, 450, accuracy: 0.001)
    }

    func testHandOcclusionCannotBeUsedAsTargetDepth() {
        let target = CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.2)
        XCTAssertFalse(NativePerceptionGeometry.handOccludes(target: target, hands: [.init(x: 0.6, y: 0.6, width: 0.2, height: 0.2)]))
        XCTAssertTrue(NativePerceptionGeometry.handOccludes(target: target, hands: [target]))
        XCTAssertTrue(NativePerceptionGeometry.handOccludes(target: target, hands: [.init(x: 0.2, y: 0.2, width: 0.11, height: 0.11)]))
        XCTAssertTrue(NativePerceptionGeometry.handOccludes(target: .zero, hands: []))
    }

    func testTrackerJumpOrAbruptScaleChangeRequiresReacquisition() {
        let previous = CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.2)
        XCTAssertTrue(NativePerceptionGeometry.validTrackedTransition(from: previous, to: previous.offsetBy(dx: 0.02, dy: 0)))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedTransition(from: previous, to: previous.offsetBy(dx: 0.2, dy: 0)))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedTransition(from: previous, to: .init(x: 0.2, y: 0.2, width: 0.4, height: 0.4)))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedTransition(from: previous, to: .init(x: 0.2, y: 0.2, width: 0.1, height: 0.1)))
    }

    func testTrackerRejectsLostConfidenceAndOutOfFrameBoxes() {
        let valid = CGRect(x: 0.2, y: 0.2, width: 0.1, height: 0.1)
        XCTAssertTrue(NativePerceptionGeometry.validTrackedBox(valid, confidence: 0.7))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedBox(valid, confidence: 0.69))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedBox(valid, confidence: .nan))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedBox(.init(x: -0.1, y: 0.2, width: 0.2, height: 0.2), confidence: 1))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedBox(.init(x: 0.9, y: 0.2, width: 0.2, height: 0.2), confidence: 1))
        XCTAssertFalse(NativePerceptionGeometry.validTrackedBox(.zero, confidence: 1))
    }
}

final class NativeRearCameraFormatPolicyTests: XCTestCase {
    func testUltraWideWinsWithoutChangingARKitQualityOrderWithinLens() {
        let formats: [NativeRearCameraFormatPolicy.Format] = [
            .init(position: .back, deviceType: .builtInWideAngleCamera),
            .init(position: .back, deviceType: .builtInUltraWideCamera),
            .init(position: .back, deviceType: .builtInUltraWideCamera)
        ]
        XCTAssertEqual(NativeRearCameraFormatPolicy.preferredIndex(in: formats), 1)
        XCTAssertNil(NativeRearCameraFormatPolicy.description(for: .builtInUltraWideCamera).fallbackReason)
    }

    func testUnsupportedUltraWideFallsBackToWideBeforeTelephoto() {
        let formats: [NativeRearCameraFormatPolicy.Format] = [
            .init(position: .back, deviceType: .builtInTelephotoCamera),
            .init(position: .back, deviceType: .builtInWideAngleCamera),
            .init(position: .back, deviceType: .builtInWideAngleCamera)
        ]
        XCTAssertEqual(NativeRearCameraFormatPolicy.preferredIndex(in: formats), 1)
        let description = NativeRearCameraFormatPolicy.description(for: .builtInWideAngleCamera)
        XCTAssertEqual(description.lens, "Wide rear camera")
        XCTAssertNotNil(description.fallbackReason)
    }

    func testFrontUltraWideCannotReplaceRearCamera() {
        let formats: [NativeRearCameraFormatPolicy.Format] = [
            .init(position: .front, deviceType: .builtInUltraWideCamera),
            .init(position: .back, deviceType: .builtInWideAngleCamera)
        ]
        XCTAssertEqual(NativeRearCameraFormatPolicy.preferredIndex(in: formats), 1)
        XCTAssertNil(NativeRearCameraFormatPolicy.preferredIndex(in: [formats[0]]))
        XCTAssertNil(NativeRearCameraFormatPolicy.preferredIndex(in: []))
    }

    func testUnknownRearFormatRetainsARKitPreferenceAndDoesNotClaimUltraWide() {
        let type = AVCaptureDevice.DeviceType(rawValue: "FutureRearCamera")
        let formats: [NativeRearCameraFormatPolicy.Format] = [
            .init(position: .unspecified, deviceType: .builtInUltraWideCamera),
            .init(position: .back, deviceType: type)
        ]
        XCTAssertEqual(NativeRearCameraFormatPolicy.preferredIndex(in: formats), 1)
        let description = NativeRearCameraFormatPolicy.description(for: type)
        XCTAssertEqual(description.lens, "ARKit rear camera (FutureRearCamera)")
        XCTAssertNotNil(description.fallbackReason)
    }
}
