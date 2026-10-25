import XCTest
import Targets
@testable import Tasks

/// All R1 data below is SIMULATED. No sensor, provider, speech or BLE calls.
final class TargetHandoffTests: XCTestCase {
    private let image = ImageInfo(widthPx: 640, heightPx: 480)
    private func proposal(label: String = "5", safety: Tasks.ControlSafety = .ordinary,
                          payment: Bool = false, frame: String = "SIMULATED-old",
                          orientation: Bool = true) throws -> TargetHandoff {
        let model = Targets.ScreenModel(frameId: "SIMULATED-old", capturedAtMs: 1_000,
            panelType: .elevatorPanel, source: .simulated,
            controls: [InteractiveElement(id: "5", label: label, type: .button,
                region: BoundingBox(x: 0.2, y: 0.4, width: 0.2, height: 0.1))],
            isPaymentScreen: payment, image: image)
        return try TargetHandoffAdapter.prepare(selection: TargetSelector().select(label, in: model),
            model: model, capture: TargetCapture(frameID: frame, capturedAtUTCms: 1_000,
                geometryID: "SIMULATED-camera", uprightImage: ImageInfo(widthPx: 640,
                    heightPx: 480, orientationApplied: orientation),
                crop: BoundingBox(x: 0.1, y: 0.2, width: 0.5, height: 0.5)), safety: safety)
    }

    private func measurement(age: Int64 = 0, frame: String = "SIMULATED-new",
        depthFrame: String = "SIMULATED-new", handFrame: String = "SIMULATED-new",
        depth: Bool = true, target: Bool = true, hand: Bool = true, tracking: Bool = true,
        confidence: Double = 0.9, identity: Bool = true, safety: Tasks.ControlSafety = .ordinary,
        position: [Double]? = [0, 0, 1]) -> R1TargetMeasurement {
        let observed = 20_000 - age
        return R1TargetMeasurement(evidence: PhysicalEvidence(targetID: "5", frameID: frame,
            observedAtMs: observed, targetVisible: target, handVisible: hand,
            trackingNormal: tracking, depthUsable: depth, outputReady: true),
            geometryID: "SIMULATED-camera", depthFrameID: depthFrame, handFrameID: handFrame,
            depthObservedAtMs: observed, handObservedAtMs: observed,
            coordinateFrameID: "SIMULATED-world", positionM: position, confidence: confidence,
            identityRevalidated: identity, safety: safety)
    }

    private func failure(_ m: R1TargetMeasurement?, _ expected: HandoffFailure,
                         file: StaticString = #filePath, line: UInt = #line) throws {
        switch TargetEvidenceGate.evaluate(try proposal(), measurement: m, nowMs: 20_000) {
        case .accepted: XCTFail("Unexpected acceptance", file: file, line: line)
        case .reacquire(let reason), .rejected(let reason):
            XCTAssertEqual(reason, expected, file: file, line: line)
        }
    }

    func testCropMappingPreservesHistoricalMetadata() throws {
        let p = try proposal()
        XCTAssertEqual(p.target.region.x, 0.2, accuracy: 0.00001)
        XCTAssertEqual(p.target.region.y, 0.4, accuracy: 0.00001)
        XCTAssertEqual(p.target.region.width, 0.1, accuracy: 0.00001)
        XCTAssertEqual(p.capture.capturedAtUTCms, 1_000)
        XCTAssertEqual(p.source, .simulated)
    }

    func testDelayedGeminiHintRequiresFreshReacquisition() throws {
        try failure(nil, .reacquisitionRequired)
        if case .accepted = TargetEvidenceGate.evaluate(try proposal(), measurement: measurement(), nowMs: 20_000) {}
        else { XCTFail("Fresh SIMULATED R1 evidence should be accepted") }
    }

    func testFreshnessBoundaryAndFutureEvidence() throws {
        if case .accepted = TargetEvidenceGate.evaluate(try proposal(), measurement: measurement(age: 300), nowMs: 20_000) {}
        else { XCTFail("300ms boundary") }
        try failure(measurement(age: 301), .staleEvidence)
        try failure(measurement(age: -1), .staleEvidence)
    }

    func testMissingDepthAndInvalidPosition() throws {
        try failure(measurement(depth: false), .missingDepth)
        try failure(measurement(position: nil), .missingDepth)
        try failure(measurement(position: [0, .nan, 1]), .missingDepth)
    }

    func testAlignmentAndOldFrameCannotPass() throws {
        try failure(measurement(depthFrame: "SIMULATED-other"), .frameMismatch)
        try failure(measurement(handFrame: "SIMULATED-other"), .frameMismatch)
        try failure(measurement(frame: "SIMULATED-old"), .frameMismatch)
        XCTAssertThrowsError(try proposal(frame: "SIMULATED-other"))
        XCTAssertThrowsError(try proposal(orientation: false))
    }

    func testLostMeasurementsStopAfterAnAcceptance() throws {
        // Gate has no retained acceptance: every update must pass independently.
        _ = TargetEvidenceGate.evaluate(try proposal(), measurement: measurement(), nowMs: 20_000)
        try failure(measurement(target: false), .trackingLost)
        try failure(measurement(hand: false), .trackingLost)
        try failure(measurement(tracking: false), .trackingLost)
        try failure(measurement(confidence: 0.1), .lowConfidence)
        try failure(measurement(confidence: .nan), .lowConfidence)
        try failure(measurement(identity: false), .reacquisitionRequired)
    }

    func testUnsafeControlsFailClosed() throws {
        let unsafe: [Tasks.ControlSafety] = [.ambiguous, .emergency, .payment, .purchaseAuthorization, .unknownCheckout]
        for safety in unsafe {
            XCTAssertThrowsError(try proposal(safety: safety))
            try failure(measurement(safety: safety), .unsafeControl)
        }
        // "bell" is how the R3 prompt names a symbol-only alarm button.
        for label in ["Alarm", "Emergency", "Pay", "Checkout", "Confirm purchase", "", "bell", "Stop", "Fire"] {
            XCTAssertThrowsError(try proposal(label: label))
        }
        XCTAssertThrowsError(try proposal(payment: true))
    }

    func testChangedOrAmbiguousModelCannotReuseSelection() throws {
        let control = InteractiveElement(id: "5", label: "5", type: .button,
            region: BoundingBox(x: 0.1, y: 0.1, width: 0.1, height: 0.1))
        let model = Targets.ScreenModel(frameId: "SIMULATED-old", capturedAtMs: 1_000,
            panelType: .elevatorPanel, source: .simulated, controls: [control], image: image)
        let selection = TargetSelector().select("5", in: model)
        let ambiguous = model.replacingControls([control, InteractiveElement(id: "twin",
            label: "5", type: .button, region: control.region)])
        let capture = try proposal().capture
        XCTAssertThrowsError(try TargetHandoffAdapter.prepare(selection: selection,
            model: ambiguous, capture: capture, safety: .ordinary))
        let unmapped = TargetCapture(frameID: capture.frameID,
            capturedAtUTCms: capture.capturedAtUTCms, geometryID: "", uprightImage: image,
            crop: capture.crop)
        XCTAssertThrowsError(try TargetHandoffAdapter.prepare(selection: selection,
            model: model, capture: unmapped, safety: .ordinary))
    }
}
