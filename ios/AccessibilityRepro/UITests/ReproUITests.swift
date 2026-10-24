import XCTest

final class ReproUITests: XCTestCase {
    func testSamplingPartiallyVisible() throws { try sample([]) }
    func testSamplingFullyVisible() throws { try sample(["--aligned"]) }
    func testSamplingWithoutFooter() throws { try sample(["--no-footer"]) }
    private func sample(_ arguments: [String]) throws {
        let app = XCUIApplication()
        app.launchArguments = ["--sampling", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"] + arguments
        app.launch()
        XCTAssertTrue(app.staticTexts["sample.message"].waitForExistence(timeout: 5))
        print("SAMPLE message=\(app.staticTexts["sample.message"].frame) viewport=\(app.scrollViews["sample.scroll"].frame)")
        attach(app, "Sampling before audit")
        try app.performAccessibilityAudit(for: .contrast) { issue in
            print("SAMPLE ISSUE \(issue.element?.debugDescription ?? "none")")
            return false
        }
    }

    func testLargestTextFirstAuditWithFooter() throws { try run(footer: true, largest: true, combined: true, warmup: false) }
    func testLargestTextCombinedAuditWithFooter() throws { try run(footer: true, largest: true, combined: true) }

    private func run(footer: Bool, largest: Bool, combined: Bool = false, warmup: Bool = true) throws {
        continueAfterFailure = true
        let app = XCUIApplication()
        if largest { app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"] }
        if !footer { app.launchArguments += ["--no-footer"] }
        app.launch()
        if warmup { try app.performAccessibilityAudit(for: [.contrast, .elementDetection, .hitRegion, .sufficientElementDescription, .textClipped, .trait]) }
        app.buttons["repro.start"].tap()
        let scroll = app.scrollViews["repro.scroll"]
        let message = app.staticTexts["repro.message"]
        XCTAssertTrue(message.waitForExistence(timeout: 5))
        attach(app, "Initial")
        print("INITIAL message=\(message.frame) viewport=\(scroll.frame)")
        let send = app.buttons["repro.send"]
        for _ in 0..<8 where !send.isHittable { scroll.swipeUp() }
        if footer {
            for _ in 0..<8 where send.frame.maxY > app.buttons["repro.pause"].frame.minY { scroll.swipeUp() }
        }
        XCTAssertTrue(send.isHittable)
        attach(app, "Before audit")
        print("BEFORE message=\(message.frame) viewport=\(scroll.frame)")
        try app.performAccessibilityAudit(for: combined ? [.contrast, .elementDetection, .hitRegion, .sufficientElementDescription, .textClipped, .trait] : .contrast) { issue in
            let details = "\(issue.compactDescription)\n\(issue.detailedDescription)\n\(issue.element?.debugDescription ?? "none")\nviewport=\(scroll.frame)"
            print(details)
            let attachment = XCTAttachment(string: details)
            attachment.name = "Issue bounds"
            attachment.lifetime = .keepAlways
            self.add(attachment)
            self.attach(app, "At issue")
            return false
        }
        attach(app, "After audit")
    }
    private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
