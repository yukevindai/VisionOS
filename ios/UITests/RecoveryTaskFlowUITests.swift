import XCTest

/// UI fixtures exercise recovery transitions without real perception or purchases.
final class RecoveryTaskFlowUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testKioskWrongScreenReacquiresBackAndContinuesToPayment() {
        let app = launch(scenario: "kiosk-wrong-screen")
        send("order at the kiosk", in: app)
        expectCaption("Tea. Say your choice", in: app)
        send("Tea", in: app)
        // The unexpected screen has a fresh Back control. The next confirmation
        // is for that recovery tap; no stale Tea coordinate can reach this page.
        expectCaption("Did the requested screen open after your tap?", in: app)
        answer(true, in: app)
        expectCaption("Tea after Back. Say your choice", in: app)
        send("Tea after Back", in: app)
        expectCaption("Did the requested screen open after your tap?", in: app)
        answer(true, in: app)
        expectCaption("Add sandwich. Say your choice", in: app)
        send("Add sandwich", in: app)
        expectCaption("Payment or purchase authorization is next. I have stopped guidance.", in: app)
        XCTAssertTrue(app.staticTexts["session.caption"].label.contains("8 dollars"))
        XCTAssertFalse(app.buttons["Yes"].exists)
        end(app)
    }

    func testKioskResetRequiresExplicitRestartBeforeContinuing() {
        let app = launch(scenario: "kiosk-reset")
        send("order at the kiosk", in: app)
        expectCaption("Tea. Say your choice", in: app)
        send("Tea", in: app)
        expectCaption("Would you like to restart the kiosk task?", in: app)
        XCTAssertFalse(app.staticTexts["session.caption"].label.contains("complete"))
        answer(true, in: app)
        expectCaption("Tea after restart. Say your choice", in: app)
        send("Tea after restart", in: app)
        expectCaption("Did the requested screen open after your tap?", in: app)
        answer(true, in: app)
        expectCaption("Add sandwich. Say your choice", in: app)
        send("Add sandwich", in: app)
        expectCaption("Payment or purchase authorization is next. I have stopped guidance.", in: app)
        end(app)
    }

    func testBlockedDoorExplainsStopThenAllowsCancelledTaskReplacement() {
        let app = launch(scenario: "blocked-door")
        send("open the door", in: app)
        XCTAssertTrue(waitForLabel(app.buttons["session.pause"], containing: "Resume assistance"))
        XCTAssertFalse(app.buttons["Yes"].exists)
        let explanation = app.staticTexts["session.message"]
        XCTAssertTrue(waitForLabel(explanation, containing: "locked, heavy, or blocked"),
                      "A blocked door must retain its explanation after pausing. Got: \(explanation.label)")
        XCTAssertTrue(explanation.label.contains("help"))
        attach(app, "Blocked door safely paused")
        send("cancel this task", in: app)
        app.buttons["session.pause"].tap()
        send("open the other door", in: app)
        expectCaption("Is the door open? This does not confirm that the path is clear.", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        end(app)
    }

    private func launch(scenario: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting", "--ui-task-scenario", scenario]
        app.launch()
        selectBlindRole(in: app)
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        return app
    }

    private func send(_ text: String, in app: XCUIApplication) {
        sendSimulatedRequest(text, in: app)
    }

    private func answer(_ yes: Bool, in app: XCUIApplication) {
        let button = app.buttons[yes ? "Yes" : "No"]
        reveal(button, in: app)
        button.tap()
    }

    private func expectCaption(_ text: String, in app: XCUIApplication) {
        let caption = app.staticTexts["session.caption"]
        XCTAssertTrue(waitForLabel(caption, containing: text), "Expected \(text); got \(caption.label)")
        attach(app, text)
    }

    private func waitForLabel(_ element: XCUIElement, containing text: String) -> Bool {
        let predicate = NSPredicate(format: "exists == true AND label CONTAINS[c] %@", text)
        return XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: element)], timeout: 5) == .completed
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        if app.scrollViews["session.content"].exists && !app.buttons["settings.done"].exists {
            revealSessionElement(element, in: app)
            return
        }
        XCTAssertTrue(element.waitForExistence(timeout: 5))
        let scroll = app.scrollViews["session.content"]
        for _ in 0..<8 {
            if element.isHittable { break }
            scroll.swipeUp()
        }
        for _ in 0..<8 {
            if element.isHittable { break }
            scroll.swipeDown()
        }
        XCTAssertTrue(element.isHittable)
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }

    private func end(_ app: XCUIApplication) {
        app.buttons["session.end"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
    }
}
