import XCTest

final class TargetReaderUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testImageReaderEntryAndCloseKeepNormalAssistanceIdle() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting"]
        app.launch()
        selectBlindRole(in: app)
        openHomeTools(in: app)
        let entry = app.buttons["home.targetReader"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        let close = app.buttons["targetReader.close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Read a button panel or screen"].exists)
        XCTAssertTrue(app.staticTexts["targetReader.message"].exists)
        XCTAssertFalse(app.staticTexts["targetReader.snapshotLabel"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
        XCTAssertFalse(app.buttons["video.end"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Image reader initial state; no captured image or live guidance"
        attachment.lifetime = .keepAlways
        add(attachment)
        close.tap()
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        app.buttons["home.tools.close"].tap()
        XCTAssertTrue(app.buttons["home.start"].exists)
        XCTAssertFalse(app.buttons["session.end"].exists)
    }

    func testSimulationDoesNotExposeTheLiveImageReader() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitesting", "--simulate"]
        app.launch()
        selectBlindRole(in: app)
        XCTAssertTrue(app.buttons["home.start"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["simulation.banner"].exists)
        XCTAssertFalse(app.buttons["home.targetReader"].exists)
    }
}
