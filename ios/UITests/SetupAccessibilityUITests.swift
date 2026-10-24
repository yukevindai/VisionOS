import XCTest

final class SetupAccessibilityUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testCalibrationStopRemainsVisibleWithLargestText() throws {
        let app = launch()
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        app.buttons["home.settings"].tap()
        reveal(app.buttons["settings.calibration"], in: app)
        app.buttons["settings.calibration"].tap()
        reveal(app.buttons["calibration.start"], in: app)
        app.buttons["calibration.start"].tap()
        let stop = app.buttons["calibration.stop"]
        XCTAssertTrue(stop.waitForExistence(timeout: 3))
        attach(app, "Calibration after Begin at largest text")
        XCTAssertTrue(stop.isHittable, "Stop must be immediately reachable after a pattern starts, without scrolling through answers.")
        let scroll = app.collectionViews.firstMatch
        scroll.swipeUp()
        XCTAssertTrue(stop.isHittable, "Stop must remain reachable while browsing pattern answers.")
        stop.tap()
        reveal(app.staticTexts["Calibration stopped."], in: app)
        XCTAssertFalse(stop.exists)
        XCTAssertTrue(app.buttons["calibration.start"].exists)
    }

    func testSetupInstructionsPermissionsAndPreferencesAtLargestText() throws {
        let app = launch(permissionScenario: "denied")
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["setup.cancel"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons["setup.cancel"].isHittable)
        app.buttons["setup.cancel"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 3))
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["setup.cancel"].waitForExistence(timeout: 3))
        attach(app, "Setup instructions at largest text")
        let explanation = app.staticTexts["setup.permission.explanation"]
        reveal(explanation, in: app)
        XCTAssertTrue(explanation.label.contains("Camera access is denied."))
        XCTAssertTrue(explanation.label.contains("Microphone access is denied."))
        let recovery = app.buttons["setup.permission.simulatedRecovery"]
        reveal(recovery, in: app)
        recovery.tap()
        reveal(explanation, in: app, towardTop: true)
        XCTAssertEqual(explanation.label, "Camera and microphone access are enabled.")
        let left = app.buttons["Left"]
        reveal(left, in: app)
        left.tap()
        let mounted = app.buttons["Mounted"]
        reveal(mounted, in: app)
        mounted.tap()
        attach(app, "Setup preferences at largest text")
        let proceed = app.buttons["setup.continue"]
        reveal(proceed, in: app)
        XCTAssertTrue(proceed.isEnabled)
        proceed.tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        app.buttons["session.end"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
    }

    func testSettingsAndBandInstructionsAtLargestText() throws {
        let app = launch()
        app.buttons["home.settings"].tap()
        attach(app, "Settings preferences at largest text")
        reveal(app.buttons["settings.bands"], in: app)
        app.buttons["settings.bands"].tap()
        let summary = app.staticTexts["bands.summary"]
        XCTAssertTrue(summary.waitForExistence(timeout: 3))
        XCTAssertTrue(summary.label.contains("SIMULATED"))
        let instructions = app.staticTexts["bands.instructions"]
        reveal(instructions, in: app)
        XCTAssertTrue(instructions.label.contains("confirm each physical side"))
        attach(app, "Band instructions at largest text")
        let discover = app.buttons["bands.discover"]
        reveal(discover, in: app)
        discover.tap()
        XCTAssertEqual(app.buttons.matching(identifier: "bands.confirm.left").count, 0,
                       "Simulation must not fabricate physical band discoveries.")
        XCTAssertEqual(app.buttons.matching(identifier: "bands.confirm.right").count, 0)
    }

    func testCalibrationAnswersRetryCompletionAndExitAtLargestText() throws {
        let app = launch()
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        app.buttons["home.settings"].tap()
        reveal(app.buttons["settings.calibration"], in: app)
        app.buttons["settings.calibration"].tap()
        reveal(app.buttons["calibration.start"], in: app)
        app.buttons["calibration.start"].tap()
        let repeatPattern = app.buttons["calibration.repeat"]
        reveal(repeatPattern, in: app)
        repeatPattern.tap()
        let wrong = app.buttons["calibration.answer.Right"]
        reveal(wrong, in: app)
        wrong.tap()
        let feedback = app.staticTexts["calibration.message"]
        reveal(feedback, in: app, towardTop: true)
        XCTAssertTrue(feedback.label.contains("did not match"))
        XCTAssertEqual(app.staticTexts["calibration.progress"].label, "Check 1 of 11")
        let answers = ["Left", "Right", "Move left", "Move right", "Move up", "Move down", "Closer", "On target", "Success", "Stop", "Lost target or hand"]
        for answer in answers {
            let button = app.buttons["calibration.answer.\(answer)"]
            reveal(button, in: app)
            XCTAssertTrue(app.buttons["calibration.stop"].isHittable)
            button.tap()
        }
        reveal(feedback, in: app, towardTop: true)
        XCTAssertEqual(feedback.label, "SIMULATED calibration complete. Real bands remain uncalibrated.")
        XCTAssertFalse(app.buttons["calibration.stop"].exists)
        attach(app, "Completed simulated calibration at largest text")
        reveal(app.buttons["calibration.start"], in: app)
        app.buttons["calibration.start"].tap()
        XCTAssertTrue(app.buttons["calibration.stop"].waitForExistence(timeout: 3))
        app.navigationBars.buttons["BackButton"].tap()
        app.buttons["settings.done"].tap()
        XCTAssertEqual(app.buttons["session.pause"].label, "Resume assistance")
        app.buttons["home.settings"].tap()
        reveal(app.buttons["settings.calibration"], in: app)
        app.buttons["settings.calibration"].tap()
        reveal(feedback, in: app, towardTop: true)
        XCTAssertEqual(feedback.label, "Calibration stopped.")
        XCTAssertFalse(app.buttons["calibration.stop"].exists)
    }

    func testSettingsPauseRemainsReachableOnBandPageWithLargestText() {
        let app = launch()
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        app.buttons["home.settings"].tap()
        let pause = app.buttons["settings.pause"]
        XCTAssertTrue(pause.waitForExistence(timeout: 3))
        XCTAssertTrue(pause.isHittable)
        reveal(app.buttons["settings.bands"], in: app)
        app.buttons["settings.bands"].tap()
        XCTAssertTrue(app.staticTexts["bands.summary"].waitForExistence(timeout: 3))
        XCTAssertTrue(pause.isHittable)
        attach(app, "Pause reachable on wristband setup at largest text")
        pause.tap()
        XCTAssertFalse(pause.exists)
        app.navigationBars.buttons["BackButton"].tap()
        app.buttons["settings.done"].tap()
        XCTAssertEqual(app.buttons["session.pause"].label, "Resume assistance")
        app.buttons["session.end"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
    }

    func testSetupAuditWithLargestText() throws {
        let app = launch(permissionScenario: "denied")
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["setup.cancel"].waitForExistence(timeout: 3))
        try audit(app, "Setup")
    }

    func testSettingsAuditWithLargestText() throws {
        let app = launch()
        app.buttons["home.settings"].tap()
        XCTAssertTrue(app.buttons["settings.done"].waitForExistence(timeout: 3))
        try audit(app, "Settings")
    }

    func testBandSetupAuditWithLargestText() throws {
        let app = launch()
        app.buttons["home.settings"].tap()
        reveal(app.buttons["settings.bands"], in: app)
        app.buttons["settings.bands"].tap()
        XCTAssertTrue(app.staticTexts["bands.summary"].waitForExistence(timeout: 3))
        try audit(app, "Wristband setup")
    }

    func testActiveCalibrationAuditWithLargestText() throws {
        try auditActiveCalibration(appearance: "light")
    }

    func testActiveCalibrationAuditInDarkAppearanceWithLargestText() throws {
        try auditActiveCalibration(appearance: "dark")
    }

    private func auditActiveCalibration(appearance: String) throws {
        let app = launch(appearance: appearance)
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        app.buttons["home.settings"].tap()
        reveal(app.buttons["settings.calibration"], in: app)
        app.buttons["settings.calibration"].tap()
        reveal(app.buttons["calibration.start"], in: app)
        app.buttons["calibration.start"].tap()
        XCTAssertTrue(app.buttons["calibration.stop"].waitForExistence(timeout: 3))
        reveal(app.buttons["calibration.answer.Left"], in: app)
        attach(app, "Active calibration answers and Stop in \(appearance) appearance")
        try audit(app, "Active calibration \(appearance)")
    }

    private func audit(_ app: XCUIApplication, _ screen: String) throws {
        try app.performAccessibilityAudit(for: .all) { issue in
            print("\(screen): \(issue.auditType), \(issue.compactDescription), element \(String(describing: issue.element))")
            return false
        }
    }

    private func launch(permissionScenario: String? = nil, appearance: String = "light") -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting", "--ui-appearance", appearance,
                               "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        if let permissionScenario { app.launchArguments += ["--ui-permission-scenario", permissionScenario] }
        app.launch()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
        return app
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication, towardTop: Bool = false) {
        let scroll = app.collectionViews.firstMatch.exists ? app.collectionViews.firstMatch : app.scrollViews.firstMatch
        for attempt in 0..<40 {
            let top = (app.navigationBars.allElementsBoundByIndex.map { $0.frame.maxY }.max() ?? app.frame.minY) + 8
            let footerIDs = ["calibration.stop", "settings.pause", "settings.done", "setup.cancel"]
            let footerTop = footerIDs.map { app.buttons[$0] }.filter { $0.exists && $0.frame.minY > app.frame.midY }
                .map { $0.frame.minY }.min()
            let bottom = footerTop.map { $0 - 8 } ?? app.frame.maxY - 32
            if element.exists {
                let frame = element.frame
                if element.isHittable && frame.minY >= top && frame.maxY <= bottom { return }
                if element.elementType == .staticText && frame.height > bottom - top,
                   frame.minY >= top && frame.minY < top + 200 { return }
                if frame.minY < top {
                    scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35))
                        .press(forDuration: 0.05, thenDragTo: scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.65)))
                    continue
                }
            }
            let searchUpward = !element.exists && (towardTop ? attempt < 20 : attempt >= 20)
            scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: searchUpward ? 0.35 : 0.65))
                .press(forDuration: 0.05, thenDragTo: scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: searchUpward ? 0.65 : 0.35)))
        }
        XCTFail("Unable to fully reveal \(element)")
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
