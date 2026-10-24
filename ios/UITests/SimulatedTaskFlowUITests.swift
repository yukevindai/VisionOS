import XCTest

/// Exercises the assembled app through visible controls and explicitly simulated input.
final class SimulatedTaskFlowUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testButtonConfirmationRetryCancellationAndCompletion() {
        let app = launch()
        send("press the blue button", in: app)
        expectCaption("Did the button activate?", in: app)
        answer(false, in: app)
        expectCaption("Did the button activate?", in: app)
        cancelAndRestart("press the blue button", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        XCTAssertFalse(app.buttons["Yes"].exists)
        end(app)
    }

    func testObjectPauseResumeCancellationAndCompletion() {
        let app = launch()
        send("pick up the cup", in: app)
        expectCaption("Are you holding the requested object?", in: app)
        app.buttons["session.pause"].tap()
        XCTAssertEqual(app.buttons["session.pause"].label, "Resume assistance")
        XCTAssertFalse(app.buttons["Yes"].exists)
        app.buttons["session.pause"].tap()
        expectCaption("Are you holding the requested object?", in: app)
        cancelAndRestart("pick up the cup", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        end(app)
    }

    func testKioskClarificationCancellationTwoItemsAndPaymentBoundary() {
        let app = launch()
        send("order at the kiosk", in: app)
        expectCaption("Tea. Say your choice, more, repeat, or back.", in: app)
        send("something else", in: app)
        expectCaption("That choice is ambiguous. Please say the exact control label.", in: app)
        cancelAndRestart("order at the kiosk", in: app)
        send("Tea", in: app)
        expectCaption("Did the requested screen open after your tap?", in: app)
        answer(true, in: app)
        expectCaption("Add sandwich. Say your choice, more, repeat, or back.", in: app)
        send("Add sandwich", in: app)
        let caption = app.staticTexts["session.caption"]
        XCTAssertTrue(caption.waitForExistence(timeout: 5))
        XCTAssertTrue(waitForLabel(caption, containing: "Payment"))
        XCTAssertTrue(caption.label.contains("8 dollars"))
        XCTAssertFalse(app.buttons["Yes"].exists)
        end(app)
    }

    func testPrintedMenuSectionsQuestionsCancellationAndRestart() {
        let app = launch()
        send("read the menu", in: app)
        expectCaption("SIMULATED drinks. Tea, 3 dollars. Coffee, 4 dollars", in: app)
        send("more", in: app)
        expectCaption("SIMULATED food. Sandwich, 5 dollars", in: app)
        send("back", in: app)
        expectCaption("SIMULATED drinks. Tea, 3 dollars. Coffee, 4 dollars", in: app)
        send("what does tea cost?", in: app)
        XCTAssertTrue(waitForLabel(app.staticTexts["session.caption"], containing: "This is fixture menu data."))
        cancelAndRestart("read the menu", in: app)
        expectCaption("SIMULATED drinks. Tea, 3 dollars. Coffee, 4 dollars", in: app)
        end(app)
    }

    func testDoorRetryCancellationOpeningAndPassage() {
        let app = launch()
        send("open the door", in: app)
        expectCaption("Is the door open? This does not confirm that the path is clear.", in: app)
        answer(false, in: app)
        expectCaption("Is the door open? This does not confirm that the path is clear.", in: app)
        cancelAndRestart("open the door", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        XCTAssertFalse(app.buttons["Yes"].exists)
        end(app)
    }

    func testObjectTrackingLossRequiresRecoveryAndFreshConfirmation() {
        let app = launch()
        send("pick up the cup", in: app)
        expectCaption("Are you holding the requested object?", in: app)
        openDiagnostics(app)
        let lose = app.buttons["Simulate tracking loss"]
        reveal(lose, in: app); lose.tap()
        let restore = app.buttons["Restore fixture tracking"]
        reveal(restore, in: app); restore.tap()
        app.navigationBars.buttons["BackButton"].tap()
        app.buttons["settings.done"].tap()
        XCTAssertEqual(app.buttons["session.pause"].label, "Resume assistance")
        XCTAssertFalse(app.buttons["Yes"].exists)
        app.buttons["session.pause"].tap()
        XCTAssertTrue(waitForLabel(app.staticTexts["session.caption"], containing: "resume"))
        answer(true, in: app)
        expectCaption("Are you holding the requested object?", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        end(app)
    }

    func testLargestTextButtonRequestConfirmationAndStop() {
        let app = launch(largestText: true)
        send("press the blue button", in: app)
        XCTAssertFalse(app.keyboards.firstMatch.exists, "Sending must restore space for the confirmation.")
        reveal(app.staticTexts["session.caption"], in: app)
        expectCaption("Did the button activate?", in: app)
        answer(true, in: app)
        expectCaption("You confirmed the task is complete.", in: app)
        XCTAssertTrue(app.buttons["session.pause"].isHittable)
        XCTAssertTrue(app.buttons["session.end"].isHittable)
        end(app)
    }

    private func launch(largestText: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting"]
        if largestText {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        }
        app.launch()
        selectBlindRole(in: app)
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
        app.buttons["home.start"].tap()
        XCTAssertTrue(app.buttons["session.end"].waitForExistence(timeout: 5))
        return app
    }

    private func send(_ text: String, in app: XCUIApplication) {
        let input = app.descendants(matching: .any).matching(identifier: "fixture.input").firstMatch
        reveal(input, in: app)
        input.tap()
        input.typeText(text)
        let send = app.buttons["fixture.send"]
        reveal(send, in: app)
        send.tap()
    }

    private func answer(_ yes: Bool, in app: XCUIApplication) {
        let button = app.buttons[yes ? "Yes" : "No"]
        XCTAssertTrue(button.waitForExistence(timeout: 5))
        reveal(button, in: app)
        button.tap()
    }

    private func cancelAndRestart(_ request: String, in app: XCUIApplication) {
        send("cancel this task", in: app)
        XCTAssertEqual(app.buttons["session.pause"].label, "Resume assistance")
        XCTAssertFalse(app.buttons["Yes"].exists)
        app.buttons["session.pause"].tap()
        XCTAssertFalse(app.buttons["Yes"].exists)
        send(request, in: app)
    }

    private func expectCaption(_ text: String, in app: XCUIApplication) {
        let caption = app.staticTexts["session.caption"]
        XCTAssertTrue(waitForLabel(caption, containing: text), "Expected \(text); got \(caption.label)")
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = text; attachment.lifetime = .keepAlways; add(attachment)
    }

    private func waitForLabel(_ element: XCUIElement, containing text: String) -> Bool {
        let predicate = NSPredicate(format: "exists == true AND label CONTAINS[c] %@", text)
        return XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: element)], timeout: 5) == .completed
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        let sessionScroll = app.scrollViews["session.content"]
        let scroll = sessionScroll.exists ? sessionScroll : app.collectionViews.firstMatch
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

    private func openDiagnostics(_ app: XCUIApplication) {
        openSettings(in: app)
        let diagnostics = app.buttons["Integration diagnostics"]
        reveal(diagnostics, in: app); diagnostics.tap()
    }

    private func end(_ app: XCUIApplication) {
        app.buttons["session.end"].tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
    }
}
