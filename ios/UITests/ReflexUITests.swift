import XCTest

final class ReflexUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSimulationStartsPausesResumesAndEnds() {
        let app = launchSimulation()

        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        start.tap()

        let pause = app.buttons["session.pause"]
        XCTAssertTrue(pause.waitForExistence(timeout: 5))
        pause.tap()
        XCTAssertTrue(app.staticTexts["Paused"].waitForExistence(timeout: 3))

        pause.tap()
        XCTAssertTrue(app.staticTexts["Listening"].waitForExistence(timeout: 3))

        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 3))
        end.tap()
        XCTAssertTrue(start.waitForExistence(timeout: 5))
    }

    func testRepeatedStartDoesNotCreateAnotherSession() {
        let app = launchSimulation()
        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        start.tap()

        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        if start.exists {
            start.tap()
        }
        XCTAssertEqual(app.buttons.matching(identifier: "session.end").count, 1)
    }

    func testStartAndEndRemainAvailableWithAccessibilityTextSize() {
        let app = XCUIApplication()
        app.launchArguments = [
            "--simulate",
            "--uitesting",
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityXXXL",
        ]
        app.launch()

        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        XCTAssertTrue(start.isHittable)
        start.tap()

        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        let scrollView = app.scrollViews.firstMatch
        for _ in 0..<5 where !end.isHittable {
            scrollView.swipeUp()
        }
        XCTAssertTrue(end.isHittable)
    }

    func testDeniedPermissionsBlockStartAndRecoverWithoutSystemPrompts() {
        assertDeniedPermissionRecovery(scenario: "denied", deniedResources: ["Camera", "Microphone"])
    }

    func testCameraDenialHasSpecificRecovery() {
        assertDeniedPermissionRecovery(scenario: "camera-denied", deniedResources: ["Camera"])
    }

    func testMicrophoneDenialHasSpecificRecovery() {
        assertDeniedPermissionRecovery(scenario: "microphone-denied", deniedResources: ["Microphone"])
    }

    func testRestrictedPermissionExplainsDevicePolicyAndBlocksAssistance() {
        let app = launchSimulation(permissionScenario: "restricted")
        app.buttons["home.start"].tap()
        let explanation = app.staticTexts["setup.permission.explanation"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 3))
        XCTAssertTrue(explanation.label.contains("Camera access is restricted by device policy"))
        XCTAssertTrue(explanation.label.contains("administrator"))
        XCTAssertFalse(app.buttons["setup.permissions"].exists)
        XCTAssertFalse(app.buttons["setup.permission.simulatedRecovery"].exists)
        let proceed = app.buttons["setup.continue"]
        reveal(proceed, in: app)
        XCTAssertFalse(proceed.isEnabled)
        XCTAssertFalse(app.buttons["session.pause"].exists)
    }

    func testUnrequestedPermissionCanBeAllowedInExplicitSimulation() {
        let app = launchSimulation(permissionScenario: "requestable")
        app.buttons["home.start"].tap()
        let request = app.buttons["setup.permissions"]
        XCTAssertTrue(request.waitForExistence(timeout: 3))
        reveal(request, in: app)
        request.tap()
        let explanation = app.staticTexts["setup.permission.explanation"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 3))
        XCTAssertEqual(explanation.label, "Camera and microphone access are enabled.")
        let proceed = app.buttons["setup.continue"]
        reveal(proceed, in: app)
        XCTAssertTrue(proceed.isEnabled)
        proceed.tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
    }

    func testAccessibilityAuditInLightAppearance() throws {
        try auditSimulation(appearance: .light)
    }

    func testAccessibilityAuditInDarkAppearance() throws {
        try auditSimulation(appearance: .dark)
    }

    func testAccessibilityAuditWithLargestAccessibilityText() throws {
        try auditSimulation(appearance: .light, largestText: true)
    }

    private func assertDeniedPermissionRecovery(scenario: String, deniedResources: [String]) {
        let app = launchSimulation(permissionScenario: scenario)
        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        start.tap()
        let explanation = app.staticTexts["setup.permission.explanation"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 3))
        for resource in deniedResources {
            XCTAssertTrue(explanation.label.contains("\(resource) access is denied."))
        }
        XCTAssertTrue(app.descendants(matching: .any)["simulation.banner"].exists)
        XCTAssertFalse(app.buttons["setup.permissions"].exists)
        let proceed = app.buttons["setup.continue"]
        reveal(proceed, in: app)
        XCTAssertFalse(proceed.isEnabled)
        XCTAssertFalse(app.buttons["session.pause"].exists)

        // A second Start after cancellation must still enforce the denied state.
        app.buttons["setup.cancel"].tap()
        start.tap()
        let recover = app.buttons["setup.permission.simulatedRecovery"]
        XCTAssertTrue(recover.waitForExistence(timeout: 3))
        reveal(recover, in: app)
        recover.tap()
        XCTAssertEqual(explanation.label, "Camera and microphone access are enabled.")
        reveal(proceed, in: app)
        XCTAssertTrue(proceed.isEnabled)
        proceed.tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["session.status"].exists)
        app.buttons["session.end"].tap()
        XCTAssertTrue(start.waitForExistence(timeout: 5))
    }

    private func auditSimulation(appearance: XCUIDevice.Appearance, largestText: Bool = false) throws {
        let originalAppearance = XCUIDevice.shared.appearance
        XCUIDevice.shared.appearance = appearance
        defer { XCUIDevice.shared.appearance = originalAppearance }
        let app = launchSimulation(largestText: largestText, appearance: appearance)
        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        XCTAssertEqual(start.label, "Start assistance")
        XCTAssertTrue(start.isHittable)
        try performAudit(of: app, screen: "Home")
        start.tap()
        let pause = app.buttons["session.pause"]
        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        XCTAssertEqual(pause.label, "Pause assistance")
        XCTAssertEqual(end.label, "End assistance")
        XCTAssertTrue(pause.isHittable)
        XCTAssertTrue(end.isHittable)
        attachScreenshot(of: app, named: "Initial session")
        let send = app.buttons["fixture.send"]
        reveal(send, in: app)
        for _ in 0..<8 where send.frame.maxY > pause.frame.minY {
            app.scrollViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(send.isHittable)
        XCTAssertGreaterThanOrEqual(send.frame.width, 44)
        XCTAssertGreaterThanOrEqual(send.frame.height, 44)
        XCTAssertLessThanOrEqual(send.frame.maxY, pause.frame.minY)
        XCTAssertTrue(pause.isHittable)
        XCTAssertTrue(end.isHittable)
        try performAudit(of: app, screen: "Session with simulated input visible")
        pause.tap()
        XCTAssertEqual(pause.label, "Resume assistance")
        XCTAssertTrue(end.isHittable)
        end.tap()
        XCTAssertTrue(start.waitForExistence(timeout: 5))
    }

    private func performAudit(of app: XCUIApplication, screen: String) throws {
        attachScreenshot(of: app, named: "\(screen) before accessibility audit")
        try app.performAccessibilityAudit(for: [.contrast, .elementDetection, .hitRegion,
                                              .sufficientElementDescription, .textClipped, .trait]) { issue in
            let details = [
                "Screen: \(screen)",
                "Audit type: \(issue.auditType.rawValue)",
                "Summary: \(issue.compactDescription)",
                "Details: \(issue.detailedDescription)",
                "Element: \(issue.element?.debugDescription ?? "Unavailable")",
            ].joined(separator: "\n")
            let attachment = XCTAttachment(string: details)
            attachment.name = "\(screen) accessibility issue"
            attachment.lifetime = .keepAlways
            self.add(attachment)
            print(details)
            // Xcode 26.6 can sample a blank crop for these offscreen instructions.
            // Keep visible contrast and every other element/type as failures.
            if issue.auditType == .contrast,
               let element = issue.element,
               element.identifier == "fixture.instructions",
               !element.frame.isEmpty,
               !element.frame.intersects(app.frame) {
                print("Known offscreen fixture.instructions contrast sampling issue; see Validation/CI_ACCESSIBILITY.md")
                return true
            }
            return false
        }
    }

    private func attachScreenshot(of app: XCUIApplication, named name: String) {
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        let scroll = app.scrollViews.firstMatch.exists ? app.scrollViews.firstMatch : app.collectionViews.firstMatch
        for _ in 0..<8 where !element.isHittable { scroll.swipeUp() }
        if !element.isHittable {
            for _ in 0..<8 where !element.isHittable { scroll.swipeDown() }
        }
        XCTAssertTrue(element.isHittable)
    }

    private func launchSimulation(permissionScenario: String? = nil, largestText: Bool = false,
                                  appearance: XCUIDevice.Appearance? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting"]
        if let permissionScenario { app.launchArguments += ["--ui-permission-scenario", permissionScenario] }
        if largestText {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        }
        if let appearance { app.launchArguments += ["--ui-appearance", appearance == .dark ? "dark" : "light"] }
        app.launch()
        return app
    }
}
