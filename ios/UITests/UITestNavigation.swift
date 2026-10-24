import XCTest

extension XCTestCase {
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
