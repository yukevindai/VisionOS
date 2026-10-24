import XCTest

final class ReflexUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testFriendVideoEntryAndCloseKeepsAssistanceIdle() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting"]
        app.launch()
        selectBlindRole(in: app)
        openHomeTools(in: app)
        let entry = app.buttons["home.friendVideo"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        let close = app.buttons["video.close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Share your camera with a trusted friend"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        attachScreenshot(of: app, named: "Friend video entry")
        close.tap()
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        app.buttons["home.tools.close"].tap()
        XCTAssertTrue(app.buttons["home.start"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
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

    func testRolesKeepHelperSeparateFromTheOneButtonCallFlow() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting"]
        app.launch()
        let blind = app.buttons["role.blind"]
        let helper = app.buttons["role.helper"]
        XCTAssertTrue(blind.waitForExistence(timeout: 5))
        XCTAssertTrue(helper.isHittable)
        let screenArea = app.frame.width * app.frame.height
        let blindArea = blind.frame.width * blind.frame.height / screenArea
        let helperArea = helper.frame.width * helper.frame.height / screenArea
        XCTAssertGreaterThan(blindArea, 0.40, "Blind should occupy about half the screen.")
        XCTAssertLessThan(blindArea, 0.60)
        XCTAssertGreaterThan(helperArea, 0.11, "Not blind should occupy about 15 percent of the screen.")
        XCTAssertLessThan(helperArea, 0.20)
        XCTAssertLessThanOrEqual(blind.frame.maxY, helper.frame.minY)
        XCTAssertFalse(app.staticTexts["Choose the experience that works for you."].exists)
        XCTAssertFalse(app.buttons["home.start"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        attachScreenshot(of: app, named: "Choose Blind or Not blind")

        helper.tap()
        XCTAssertTrue(app.descendants(matching: .any)["helper.sessions"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["home.start"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        attachScreenshot(of: app, named: "Helper shared sessions without starting owner assistance")

        app.buttons["home.changeRole"].tap()
        selectBlindRole(in: app)
        let call = app.buttons["home.start"]
        XCTAssertEqual(call.label, "Call")
        XCTAssertGreaterThan(call.frame.width, app.frame.width * 0.85)
        XCTAssertGreaterThan(call.frame.height, app.frame.height * 0.65)
        XCTAssertTrue(call.isHittable)
        XCTAssertFalse(app.staticTexts["Ready when you are."].exists)
        XCTAssertFalse(app.buttons["home.friendVideo"].exists)
        XCTAssertFalse(app.buttons["home.targetReader"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        attachScreenshot(of: app, named: "One Call action")

        app.terminate()
        app.launch()
        XCTAssertTrue(blind.waitForExistence(timeout: 5))
        XCTAssertTrue(helper.exists)
        XCTAssertFalse(app.buttons["home.start"].exists)
    }

    func testCallControlsRemainReachableWithReducedTransparency() {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting", "--ui-reduce-transparency"]
        app.launch()
        selectBlindRole(in: app)
        app.buttons["home.start"].tap()
        let stop = app.buttons["session.pause"]
        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        XCTAssertTrue(stop.isHittable)
        XCTAssertTrue(end.isHittable)
        attachScreenshot(of: app, named: "Reduce Transparency camera controls")
        stop.tap()
        XCTAssertEqual(stop.label, "Resume assistance")
        XCTAssertTrue(end.isHittable)
        end.tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
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
        attachScreenshot(of: app, named: "Largest text role selection")
        let helper = app.buttons["role.helper"]
        reveal(helper, in: app)
        XCTAssertTrue(helper.isHittable)
        reveal(app.buttons["role.blind"], in: app)
        selectBlindRole(in: app)

        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        XCTAssertTrue(start.isHittable)
        attachScreenshot(of: app, named: "Largest text Call")
        start.tap()

        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        let scrollView = app.scrollViews.firstMatch
        for _ in 0..<5 where !end.isHittable {
            scrollView.swipeUp()
        }
        XCTAssertTrue(end.isHittable)
        XCTAssertTrue(app.buttons["session.pause"].isHittable)
        XCTAssertTrue(app.buttons["session.details"].isHittable)
        XCTAssertTrue(app.staticTexts["simulation.banner"].isHittable)
        attachScreenshot(of: app, named: "Largest text camera controls")
        end.tap()
        XCTAssertTrue(start.waitForExistence(timeout: 5))
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
        let start = app.buttons["home.start"]
        start.tap()
        let explanation = app.staticTexts["session.error"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 3))
        XCTAssertTrue(explanation.label.contains("Camera access is restricted by device policy"))
        XCTAssertTrue(explanation.label.contains("administrator"))
        XCTAssertTrue(start.isEnabled)
        XCTAssertFalse(app.buttons["home.permission.simulatedRecovery"].exists)
        XCTAssertFalse(app.buttons["setup.continue"].exists)
        XCTAssertFalse(app.buttons["setup.permissions"].exists)
        XCTAssertFalse(app.buttons["session.pause"].exists)
    }

    func testUnrequestedPermissionCanBeAllowedInExplicitSimulation() {
        let app = launchSimulation(permissionScenario: "requestable")
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["session.status"].exists)
        XCTAssertFalse(app.buttons["setup.continue"].exists)
        XCTAssertFalse(app.buttons["setup.permissions"].exists)
        app.buttons["session.end"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
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
        let explanation = app.staticTexts["session.error"]
        XCTAssertTrue(explanation.waitForExistence(timeout: 3))
        for resource in deniedResources {
            XCTAssertTrue(explanation.label.contains("\(resource) access is denied."))
        }
        XCTAssertTrue(app.descendants(matching: .any)["simulation.banner"].exists)
        XCTAssertTrue(start.isEnabled)
        XCTAssertFalse(app.buttons["setup.continue"].exists)
        XCTAssertFalse(app.buttons["setup.permissions"].exists)
        XCTAssertFalse(app.buttons["session.pause"].exists)

        reveal(start, in: app)
        start.tap()
        XCTAssertTrue(explanation.exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        let recover = app.buttons["home.permission.simulatedRecovery"]
        XCTAssertTrue(recover.waitForExistence(timeout: 3))
        reveal(recover, in: app)
        recover.tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["session.status"].exists)
        XCTAssertFalse(app.buttons["setup.continue"].exists)
        app.buttons["session.end"].tap()
        XCTAssertTrue(start.waitForExistence(timeout: 5))
    }

    private func auditSimulation(appearance: XCUIDevice.Appearance, largestText: Bool = false) throws {
        let originalAppearance = XCUIDevice.shared.appearance
        XCUIDevice.shared.appearance = appearance
        defer { XCUIDevice.shared.appearance = originalAppearance }
        let app = launchSimulation(largestText: largestText, appearance: appearance)
        app.buttons["home.changeRole"].tap()
        try performAudit(of: app, screen: "Role selection")
        selectBlindRole(in: app)
        let start = app.buttons["home.start"]
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        XCTAssertEqual(start.label, "Call")
        XCTAssertTrue(start.isHittable)
        try performAudit(of: app, screen: "Home")
        start.tap()
        let pause = app.buttons["session.pause"]
        let end = app.buttons["session.end"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        XCTAssertEqual(pause.label, "Stop assistance")
        XCTAssertEqual(end.label, "End call")
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
        XCTAssertTrue(app.staticTexts["simulation.banner"].isHittable,
                      "Simulation provenance must stay visible while session content scrolls.")
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
        selectBlindRole(in: app)
        return app
    }
}
