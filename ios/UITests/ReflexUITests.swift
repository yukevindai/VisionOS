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

    private func launchSimulation() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["--simulate", "--uitesting"]
        app.launch()
        return app
    }
}
