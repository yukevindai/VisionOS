import XCTest

final class StartupSignInUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testLaunchOffersSignInBeforeRolesAndCanContinueWithoutIt() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting"]
        app.launch()
        XCTAssertTrue(app.buttons["startup.signIn"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["startup.skip"].isHittable)
        XCTAssertFalse(app.buttons["role.blind"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        attach(app, "Sign in before choosing a role")
        app.buttons["startup.skip"].tap()
        XCTAssertTrue(app.buttons["role.blind"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["role.helper"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
    }

    func testStartupAtLargestTextHasAccessibleControls() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        XCTAssertTrue(app.buttons["startup.signIn"].waitForExistence(timeout: 5))
        let scroll = app.scrollViews["startup.signInScreen"]
        let skip = app.buttons["startup.skip"]
        for _ in 0..<8 where !skip.isHittable { scroll.swipeUp() }
        XCTAssertTrue(skip.isHittable)
        attach(app, "Startup sign-in at largest text")
        try app.performAccessibilityAudit()
        skip.tap()
        XCTAssertTrue(app.buttons["role.blind"].waitForExistence(timeout: 5))
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
