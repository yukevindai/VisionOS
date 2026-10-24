import XCTest

extension XCTestCase {
    func sendSimulatedRequest(_ text: String, in app: XCUIApplication) {
        let input = app.descendants(matching: .any).matching(identifier: "fixture.input").firstMatch
        revealSessionElement(input, in: app)
        input.tap()
        input.typeText(text)
        XCTAssertEqual(input.value as? String, text)
        let send = app.buttons["fixture.send"]
        revealSessionElement(send, in: app)
        send.tap()
        // A hittable AX element can still be behind the fixed footer. Assert the
        // action actually ran before testing the downstream task state.
        let submitted = NSPredicate(format: "exists == true AND (value == %@ OR value == %@)", "", "Simulated request")
        XCTAssertEqual(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: submitted, object: input)], timeout: 5), .completed,
                       "Send must clear the simulated request after submitting it.")
        let keyboardDismissed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"),
                                                          object: app.keyboards.firstMatch)
        XCTAssertEqual(XCTWaiter.wait(for: [keyboardDismissed], timeout: 5), .completed,
                       "Send must dismiss the keyboard.")
    }

    func revealSessionElement(_ element: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(element.waitForExistence(timeout: 5))
        let scroll = app.scrollViews["session.content"]
        for _ in 0..<24 {
            let viewport = scroll.frame.intersection(app.frame)
            let keyboard = app.keyboards.firstMatch
            let bottom = min(viewport.maxY, keyboard.exists ? keyboard.frame.minY : viewport.maxY,
                             app.buttons["session.pause"].frame.minY)
            let top = viewport.minY
            guard bottom > top else { XCTFail("No visible session scrolling area"); return }
            let frame = element.frame
            if element.isHittable && frame.minY >= top && frame.maxY <= bottom { return }
            if element.elementType == .staticText, frame.height > bottom - top,
               abs(frame.minY - top) <= 12 { return }
            let missing = frame.minY < top || frame.height > bottom - top
                ? top - frame.minY : bottom - frame.maxY
            let limit = (bottom - top) * 0.4
            var distance = max(-limit, min(limit, missing))
            if abs(distance) < 12 { distance = distance < 0 ? -12 : 12 }
            let start = app.coordinate(withNormalizedOffset: .zero)
                .withOffset(CGVector(dx: viewport.midX, dy: (top + bottom) / 2))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: distance)))
        }
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Session control obscured: \(element.identifier)"
        attachment.lifetime = .keepAlways
        add(attachment)
        XCTFail("Unable to reveal \(element.identifier) inside the session viewport: \(element.frame)")
    }

    func selectBlindRole(in app: XCUIApplication) {
        let blind = app.buttons["role.blind"]
        XCTAssertTrue(blind.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["session.end"].exists)
        XCTAssertTrue(blind.isHittable)
        blind.tap()
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
    }

    func openHomeTools(in app: XCUIApplication) {
        let tools = app.buttons["home.tools"]
        XCTAssertTrue(tools.waitForExistence(timeout: 5))
        tools.tap()
    }

    func openSettings(in app: XCUIApplication) {
        if app.buttons["session.end"].exists {
            let details = app.buttons["session.details"]
            XCTAssertTrue(details.waitForExistence(timeout: 5))
            details.tap()
            let settings = app.buttons["session.settings"]
            XCTAssertTrue(settings.waitForExistence(timeout: 5))
            let scroll = app.scrollViews["session.details.content"]
            for _ in 0..<8 where !settings.isHittable { scroll.swipeUp() }
            XCTAssertTrue(settings.isHittable)
            settings.tap()
        } else {
            app.buttons["home.settings"].tap()
        }
        XCTAssertTrue(app.buttons["settings.done"].waitForExistence(timeout: 5))
    }
}
