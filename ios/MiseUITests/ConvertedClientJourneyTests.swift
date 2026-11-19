import XCTest

final class ConvertedClientJourneyTests: XCTestCase {
    @MainActor
    func testConvertOpenLinkedClientAndReturnToInquiry() throws {
        continueAfterFailure = false
        let environment = ProcessInfo.processInfo.environment
        let port = try XCTUnwrap(environment["FOCAL_JOURNEY_PORT"].flatMap(UInt16.init))
        let password = try XCTUnwrap(environment["FOCAL_JOURNEY_PASSWORD"])
        let app = XCUIApplication()
        // Ignore any last workspace saved on a developer's simulator.
        app.launchArguments = ["-mise.last-workspace-origin", "no-saved-origin"]
        app.launch()

        let workspace = app.textFields["Studio URL or slug"]
        XCTAssertTrue(workspace.waitForExistence(timeout: 15), app.debugDescription)
        workspace.tap()
        workspace.typeText("http://127.0.0.1:\(port)\n")

        let passwordField = app.secureTextFields["Password"]
        XCTAssertTrue(passwordField.waitForExistence(timeout: 15), app.debugDescription)
        passwordField.tap()
        passwordField.typeText(password + "\n")

        let inquiries = app.tabBars.buttons["Inquiries"]
        XCTAssertTrue(inquiries.waitForExistence(timeout: 20), app.debugDescription)
        inquiries.tap()
        let inquiryName = "Autumn portrait request"
        let inquiry = app.buttons.matching(
            NSPredicate(format: "label BEGINSWITH %@", inquiryName)
        ).firstMatch
        XCTAssertTrue(inquiry.waitForExistence(timeout: 15), app.debugDescription)
        inquiry.tap()
        let original = app.navigationBars[inquiryName]
        XCTAssertTrue(original.waitForExistence(timeout: 10))

        let convertLabel = "Convert to client / project"
        let convert = app.buttons[convertLabel]
        for _ in 0..<5 {
            if convert.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(convert.isHittable, app.debugDescription)
        convert.tap()
        let confirm = app.sheets.buttons[convertLabel]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10), app.debugDescription)
        confirm.tap()

        let openClient = app.buttons["Open client record"]
        for _ in 0..<5 {
            if openClient.isHittable { break }
            app.swipeDown()
        }
        XCTAssertTrue(openClient.waitForExistence(timeout: 10), app.debugDescription)
        openClient.tap()
        let record = app.navigationBars["Client record"]
        XCTAssertTrue(record.waitForExistence(timeout: 10), app.debugDescription)
        let clientName = app.staticTexts["client-record-name"]
        XCTAssertTrue(clientName.waitForExistence(timeout: 15), app.debugDescription)
        XCTAssertEqual(clientName.label, "Juniper Portrait Studio")

        record.buttons.firstMatch.tap()
        XCTAssertTrue(original.waitForExistence(timeout: 10), app.debugDescription)
        XCTAssertTrue(openClient.waitForExistence(timeout: 10), app.debugDescription)
    }
}
