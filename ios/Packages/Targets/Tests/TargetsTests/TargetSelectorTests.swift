import XCTest
@testable import Targets

final class TargetSelectorTests: XCTestCase {
    private let selector = TargetSelector()

    private func fixtureURL(_ path: String) throws -> URL {
        let root = try XCTUnwrap(Bundle.module.url(forResource: "Fixtures", withExtension: nil))
        return root.appendingPathComponent(path)
    }
    private func model(_ name: String) throws -> ScreenModel {
        try JSONDecoder().decode(ScreenModel.self, from: Data(contentsOf: fixtureURL("\(name).json")))
    }
    private var elevator: ScreenModel { get throws { try model("SIMULATED_elevator_panel") } }
    private var kiosk: ScreenModel { get throws { try model("SIMULATED_ordering_kiosk") } }

    private func replacing(_ id: String, in model: ScreenModel, region: BoundingBox?,
                           confidence: Double? = 0.9) -> ScreenModel {
        model.replacingControls(model.controls.map { element in
            guard element.id == id else { return element }
            return InteractiveElement(id: element.id, label: element.label, type: element.type, region: region,
                                      confidence: confidence, price: element.price, section: element.section)
        })
    }

    // MARK: Fixtures

    func testFixturesAreLabelledSimulated() throws {
        for fixture in [try elevator, try kiosk] {
            XCTAssertTrue(fixture.isSimulated)
            XCTAssertTrue(fixture.frameId.hasPrefix("SIMULATED"))
            XCTAssertTrue(fixture.notes?.hasPrefix("SIMULATED") == true)
            XCTAssertTrue(fixture.controls.allSatisfy { $0.region?.isValid == true })
        }
    }

    func testExampleInputOutputFiles() throws {
        struct Example: Decodable { let fixture: String; let request: String; let expected: TargetSelection }
        let directory = try fixtureURL("examples")
        let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
        XCTAssertEqual(files.count, 4)
        for file in files {
            let example = try JSONDecoder().decode(Example.self, from: Data(contentsOf: file))
            XCTAssertEqual(selector.select(example.request, in: try model(example.fixture)), example.expected,
                           file.lastPathComponent)
        }
    }

    // MARK: Required cases

    func testFindFloor5() throws {
        let result = selector.select("Find floor 5", in: try elevator)
        XCTAssertEqual(result.status, .selected)
        XCTAssertEqual(result.target?.targetId, "el-5")
        XCTAssertEqual(result.target?.source, .simulated)
        XCTAssertEqual(result.target?.coordinateSystem, .normalizedImageTopLeft)
        XCTAssertEqual(selector.select("take me to the fifth floor", in: try elevator).target?.targetId, "el-5")
        XCTAssertEqual(selector.select("floor five please", in: try elevator).target?.targetId, "el-5")
    }

    func testFindTheBigMacPrefersExactLabelOverLongerItem() throws {
        let result = selector.select("Find the Big Mac", in: try kiosk)
        XCTAssertEqual(result.status, .selected)
        XCTAssertEqual(result.target?.targetId, "item-big-mac")
        XCTAssertEqual(result.target?.match, .exact)
        XCTAssertEqual(selector.select("I want the big mac meal", in: try kiosk).target?.targetId, "item-big-mac-meal")
    }

    func testMissingTarget() throws {
        XCTAssertEqual(selector.select("Find floor 12", in: try elevator).reason, .noMatch)
        let milkshake = selector.select("Find the milkshake", in: try kiosk)
        XCTAssertEqual(milkshake.status, .notFound)
        XCTAssertNil(milkshake.target)
        XCTAssertEqual(selector.select("find the", in: try kiosk).reason, .emptyRequest)
    }

    func testNumberMustBeOnTheLabel() throws {
        // "Floor 5" must not match a bare "Floor" label, and "1" must not match "10".
        let base = try elevator
        let variant = base.replacingControls(base.controls.filter { $0.id != "el-5" } + [
            InteractiveElement(id: "x-floor", label: "Floor", type: .button,
                               region: BoundingBox(x: 0.1, y: 0.1, width: 0.1, height: 0.1)),
            InteractiveElement(id: "x-10", label: "10", type: .button,
                               region: BoundingBox(x: 0.2, y: 0.1, width: 0.1, height: 0.1))
        ])
        XCTAssertEqual(selector.select("Find floor 5", in: variant).reason, .noMatch)
        XCTAssertEqual(selector.select("Find floor 1", in: variant).target?.targetId, "el-1")
    }

    func testMultipleMatchingLabelsAreAmbiguous() throws {
        let result = selector.select("Find the fries", in: try kiosk)
        XCTAssertEqual(result.status, .ambiguous)
        XCTAssertEqual(result.reason, .multipleMatches)
        XCTAssertEqual(result.candidateIds, ["item-fries-small", "item-fries-large"])
        XCTAssertEqual(result.errorCode, "LABEL_AMBIGUOUS")
        XCTAssertNil(result.target)
    }

    func testDuplicateLabelsAreAmbiguous() throws {
        let base = try elevator
        let twin = InteractiveElement(id: "el-open-rear", label: "OPEN", type: .button,
                                      region: BoundingBox(x: 0.70, y: 0.82, width: 0.12, height: 0.08), confidence: 0.9)
        let result = selector.select("press open", in: base.replacingControls(base.controls + [twin]))
        XCTAssertEqual(result.status, .ambiguous)
        XCTAssertEqual(result.reason, .duplicateLabel)
        XCTAssertEqual(result.candidateIds, ["el-open", "el-open-rear"])
    }

    func testDuplicateWithUnlocatableTwinIsStillAmbiguous() throws {
        let base = try elevator
        let twin = InteractiveElement(id: "el-open-rear", label: "Open", type: .button, region: nil)
        let result = selector.select("press open", in: base.replacingControls(base.controls + [twin]))
        XCTAssertEqual(result.status, .ambiguous)
        XCTAssertEqual(result.reason, .duplicateLabel)
    }

    func testInvalidBoundingBoxesAreNeverSelected() throws {
        let invalid: [BoundingBox?] = [
            nil,
            BoundingBox(x: .nan, y: 0.5, width: 0.1, height: 0.1),
            BoundingBox(x: 0.5, y: 0.5, width: .infinity, height: 0.1),
            BoundingBox(x: -0.1, y: 0.5, width: 0.1, height: 0.1),
            BoundingBox(x: 0.5, y: 0.5, width: 0, height: 0.1),
            BoundingBox(x: 0.5, y: 0.5, width: 0.1, height: -0.1),
            BoundingBox(x: 0.95, y: 0.5, width: 0.1, height: 0.1),
            BoundingBox(x: 0.5, y: 0.95, width: 0.1, height: 0.1),
            BoundingBox(x: 1.2, y: 0.5, width: 0.1, height: 0.1)
        ]
        for region in invalid {
            let result = selector.select("Find floor 5", in: replacing("el-5", in: try elevator, region: region))
            XCTAssertEqual(result.status, .notFound, "\(String(describing: region))")
            XCTAssertEqual(result.reason, .invalidRegion)
            XCTAssertEqual(result.candidateIds, ["el-5"])
            XCTAssertEqual(result.errorCode, "LOW_CONFIDENCE")
        }
    }

    func testInvalidRegionFromJSONIsRejected() throws {
        let json = """
        {"schemaVersion":3,"frameId":"SIMULATED-bad-box","capturedAtMs":1,"panelType":"controlPanel",
         "availability":"available","coordinateSystem":"normalizedImageTopLeft","source":"SIMULATED",
         "sections":[],"isPaymentScreen":false,
         "controls":[{"id":"c1","label":"Start","type":"button","region":{"x":0.8,"y":0.1,"width":0.5,"height":0.1}}]}
        """
        let model = try JSONDecoder().decode(ScreenModel.self, from: Data(json.utf8))
        XCTAssertEqual(selector.select("press start", in: model).reason, .invalidRegion)
    }

    // MARK: Model states

    func testLowConfidenceSingleMatchIsAmbiguous() throws {
        let region = BoundingBox(x: 0.55, y: 0.50, width: 0.12, height: 0.08)
        let result = selector.select("Find floor 5", in: replacing("el-5", in: try elevator, region: region, confidence: 0.4))
        XCTAssertEqual(result.status, .ambiguous)
        XCTAssertEqual(result.reason, .lowConfidence)
        XCTAssertEqual(result.errorCode, "LOW_CONFIDENCE")
    }

    func testUnavailableAndPartialScreens() throws {
        let unavailable = ScreenModel(frameId: "SIMULATED-empty", capturedAtMs: 1, panelType: .atmScreen,
                                      availability: .unavailable, unavailableReason: .notInView,
                                      source: .simulated, controls: [])
        XCTAssertEqual(selector.select("Find withdraw", in: unavailable).reason, .screenUnavailable)

        let base = try kiosk
        let partial = ScreenModel(frameId: base.frameId, capturedAtMs: base.capturedAtMs, panelType: base.panelType,
                                  availability: .partial, source: base.source, controls: base.controls)
        XCTAssertEqual(selector.select("Find the milkshake", in: partial).reason, .noMatchPartialView)
        XCTAssertEqual(selector.select("Find the Big Mac", in: partial).status, .selected)
    }

    func testUnknownCoordinateSystemAndEnumsDecodeSafely() throws {
        let json = """
        {"schemaVersion":3,"frameId":"SIMULATED-pixels","capturedAtMs":1,"panelType":"vendingMachine",
         "availability":"available","coordinateSystem":"pixelsBottomLeft","source":"someNewProvider",
         "sections":[],"isPaymentScreen":false,
         "controls":[{"id":"c1","label":"Start","type":"dial","region":{"x":0.1,"y":0.1,"width":0.1,"height":0.1}}]}
        """
        let model = try JSONDecoder().decode(ScreenModel.self, from: Data(json.utf8))
        XCTAssertEqual(model.panelType, .unknown)
        XCTAssertEqual(model.source, .unknown)
        XCTAssertEqual(model.controls.first?.type, .other)
        XCTAssertEqual(selector.select("press start", in: model).reason, .unsupportedCoordinates)
    }

    func testDuplicateElementIDsAreMalformed() throws {
        let base = try elevator
        let model = base.replacingControls(base.controls + [base.controls[0]])
        XCTAssertEqual(selector.select("Find floor 5", in: model).reason, .malformedModel)
    }

    // MARK: Backend pipeline output (Step 2)

    func testBackendPipelineModelDecodesAndSelects() throws {
        // Written by backend/r3_vision from a SIMULATED Gemini-shaped reply; not a live API result.
        let model = try model("SIMULATED_gemini_pipeline_elevator")
        XCTAssertTrue(model.isSimulated)
        XCTAssertEqual(model.evidence?.live, false)
        XCTAssertEqual(model.image, ImageInfo(widthPx: 400, heightPx: 200, exifOrientation: 1, orientationApplied: true))
        XCTAssertTrue(model.controls.allSatisfy { $0.confidence == nil })

        let floor5 = selector.select("Find floor 5", in: model)
        XCTAssertEqual(floor5.target?.label, "5")
        XCTAssertEqual(floor5.target?.imageRegion, BoundingBox(x: 0.55, y: 0.5, width: 0.12, height: 0.08))
        XCTAssertEqual(selector.select("Find floor 6", in: model).reason, .lowConfidence)  // partial legibility
        XCTAssertEqual(selector.select("Find floor 7", in: model).reason, .invalidRegion)  // inverted box
        XCTAssertEqual(selector.select("Find floor 8", in: model).reason, .noMatch)  // unreadable, not guessed
        XCTAssertEqual(selector.select("press open", in: model).reason, .duplicateLabel)
    }

    func testProviderFailureIsNotReportedAsAbsentTarget() throws {
        let failed = ScreenModel(frameId: "SIMULATED-failed", capturedAtMs: 1, panelType: .unknown,
                                 availability: .unavailable, unavailableReason: .providerUnavailable,
                                 source: .simulated, controls: [])
        let result = selector.select("Find floor 5", in: failed)
        XCTAssertEqual(result.status, .providerUnavailable)
        XCTAssertEqual(result.reason, .providerUnavailable)
        XCTAssertNil(result.target)
    }

    func testButtonOutranksItsPrintedLabel() throws {
        let base = try elevator
        let plate = InteractiveElement(id: "plate-5", label: "5", type: .other,
                                       region: BoundingBox(x: 0.40, y: 0.50, width: 0.08, height: 0.06))
        let result = selector.select("Find floor 5", in: base.replacingControls([plate] + base.controls))
        XCTAssertEqual(result.target?.targetId, "el-5")
    }

    func testUnknownLegibilityIsNeverTreatedAsClear() throws {
        let json = #"{"id":"c1","label":"Start","type":"button","legibility":"mostly"}"#
        XCTAssertEqual(try JSONDecoder().decode(InteractiveElement.self, from: Data(json.utf8)).legibility, .partial)
    }

    func testSelectionRoundTripsAsJSON() throws {
        let result = selector.select("Find floor 5", in: try elevator)
        let decoded = try JSONDecoder().decode(TargetSelection.self, from: JSONEncoder().encode(result))
        XCTAssertEqual(decoded, result)
    }
}
