import XCTest

final class ConvertedClientJourneyTests: XCTestCase {
    @MainActor
    func testConvertOpenLinkedClientAndProjectAndReturnToInquiry() throws {
        continueAfterFailure = false
        let environment = ProcessInfo.processInfo.environment
        let port = try XCTUnwrap(environment["FOCAL_JOURNEY_PORT"].flatMap(UInt16.init))
        let password = try XCTUnwrap(environment["FOCAL_JOURNEY_PASSWORD"])
        let expectedProjectTitle = try XCTUnwrap(environment["FOCAL_JOURNEY_PROJECT_TITLE"])
        let app = XCUIApplication()
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
            if convert.exists && convert.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(convert.isHittable, app.debugDescription)
        convert.tap()
        let confirm = app.buttons["Confirm conversion"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 10), app.debugDescription)
        confirm.tap()

        // The studio submitted the browser form concurrently, then withheld the first
        // successful response. Replay the owner's original action from the same screen.
        let update = app.alerts["Inquiry update"]
        XCTAssertTrue(update.waitForExistence(timeout: 20), app.debugDescription)
        update.buttons["OK"].tap()
        XCTAssertTrue(convert.waitForExistence(timeout: 10), app.debugDescription)
        convert.tap()
        XCTAssertTrue(confirm.waitForExistence(timeout: 10), app.debugDescription)
        confirm.tap()

        let openClient = app.buttons["Open client record"]
        for _ in 0..<5 {
            if openClient.exists && openClient.isHittable { break }
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

        let openProject = app.buttons["Open project record"]
        let project = app.navigationBars["Project record"]
        let projectTitle = app.staticTexts["project-record-title"]
        let fields = [
            ("project-record-client", "Client", "Juniper Portrait Studio"),
            ("project-record-status", "Status", "Inquiry Received"),
            ("project-record-shoot-date", "Shoot date", "2030-10-13")
        ]
        for needsRetry in [false, true] {
            XCTAssertTrue(openProject.waitForExistence(timeout: 10), app.debugDescription)
            openProject.tap()
            XCTAssertTrue(project.waitForExistence(timeout: 10), app.debugDescription)
            if needsRetry {
                let retry = app.buttons["Try Again"]
                XCTAssertTrue(retry.waitForExistence(timeout: 15), app.debugDescription)
                XCTAssertFalse(projectTitle.exists, "An unavailable project must not show stale details")
                retry.tap()
            }
            XCTAssertTrue(projectTitle.waitForExistence(timeout: 15), app.debugDescription)
            XCTAssertEqual(projectTitle.label, expectedProjectTitle)
            for (identifier, label, value) in fields {
                let row = app.descendants(matching: .any)[identifier]
                XCTAssertTrue(row.isHittable, app.debugDescription)
                XCTAssertEqual(row.label, label)
                XCTAssertEqual(row.value as? String, value)
            }

            project.buttons.firstMatch.tap()
            XCTAssertTrue(original.waitForExistence(timeout: 10), app.debugDescription)
            XCTAssertTrue(openClient.waitForExistence(timeout: 10), app.debugDescription)
            XCTAssertTrue(openProject.waitForExistence(timeout: 10), app.debugDescription)
        }
    }
}
