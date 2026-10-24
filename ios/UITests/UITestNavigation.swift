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
}
