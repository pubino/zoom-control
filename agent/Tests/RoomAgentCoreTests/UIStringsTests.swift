import XCTest
@testable import RoomAgentCore

final class UIStringsTests: XCTestCase {
    func testPartialOverrideKeepsOtherDefaults() throws {
        let s = try ZoomUIStrings.merged(json: Data(#"{"startShareItems":["Share"]}"#.utf8))
        XCTAssertEqual(s.startShareItems, ["Share"])
        XCTAssertEqual(s.meetingMenu, ZoomUIStrings.defaults.meetingMenu)
    }

    func testUnknownKeyAndBadTypesAreErrors() {
        XCTAssertThrowsError(try ZoomUIStrings.merged(json: Data(#"{"meetingMenuu":"x"}"#.utf8)))
        XCTAssertThrowsError(try ZoomUIStrings.merged(json: Data(#"{"meetingMenu":["x"]}"#.utf8)))
        XCTAssertThrowsError(try ZoomUIStrings.merged(json: Data("[]".utf8)))
    }

    func testTileTemplates() {
        let d = DisplayInfo(id: 7, name: "Display 2", index: 2, isMain: false)
        XCTAssertEqual(ZoomUIStrings.defaults.tiles(for: d), ["Screen 2", "Desktop 2", "Display 2"])
    }

    func testLoadPrecedenceAndErrors() throws {
        let home = "/Users/av"
        let userPath = home + "/Library/Application Support/zoom-control/ui-strings.json"
        let files: [String: String] = [userPath: #"{"meetingMenu":"User"}"#,
                                       ZoomUIStrings.systemPath: #"{"meetingMenu":"System"}"#,
                                       "/x.json": #"{"meetingMenu":"Explicit"}"#, "/bad.json": "{"]
        let exists: (String) -> Bool = { files[$0] != nil }
        let read: (String) throws -> Data = { Data(files[$0]!.utf8) }

        var (s, src) = try ZoomUIStrings.load(env: [:], home: home, fileExists: exists, read: read)
        XCTAssertEqual(s.meetingMenu, "User"); XCTAssertEqual(src, userPath)

        (s, src) = try ZoomUIStrings.load(env: ["ROOMAGENT_UI_STRINGS": "/x.json"], home: home, fileExists: exists, read: read)
        XCTAssertEqual(s.meetingMenu, "Explicit")

        (s, src) = try ZoomUIStrings.load(env: [:], home: "/nobody", fileExists: exists, read: read)
        XCTAssertEqual(s.meetingMenu, "System")

        (s, src) = try ZoomUIStrings.load(env: [:], home: "/nobody", fileExists: { _ in false }, read: read)
        XCTAssertEqual(src, "defaults")

        XCTAssertThrowsError(try ZoomUIStrings.load(env: ["ROOMAGENT_UI_STRINGS": "/missing.json"], home: home,
                                                    fileExists: exists, read: read))
        XCTAssertThrowsError(try ZoomUIStrings.load(env: ["ROOMAGENT_UI_STRINGS": "/bad.json"], home: home,
                                                    fileExists: exists, read: read)) { e in
            XCTAssertEqual("\(e)".components(separatedBy: "invalid UI strings").count, 2, "single prefix")
        }
    }
}

final class CalibrationTests: XCTestCase {
    let s = ZoomUIStrings.defaults

    func testNotRunning() {
        let r = calibrationReport(ObservedUI(zoomRunning: false, windowTitles: [], menuBarTitles: [], menuItemTitles: []),
                                  strings: s, source: "defaults", expectSharing: false)
        XCTAssertEqual(r.status(of: "zoom_running"), .fail)
    }

    func testMatchingMeetingNotSharing() {
        let ui = ObservedUI(zoomRunning: true, windowTitles: ["Zoom Meeting"], menuBarTitles: ["zoom.us", "Meeting"],
                            menuItemTitles: ["Mute Audio", "Share Screen"])
        let r = calibrationReport(ui, strings: s, source: "defaults", expectSharing: false)
        XCTAssertEqual(r.status, .ok, r.json(pretty: true))
    }

    func testRenamedStringsAreReportedWithWhatWasSeen() {
        let ui = ObservedUI(zoomRunning: true, windowTitles: ["Zoom Workplace"], menuBarTitles: ["zoom.us", "Call"],
                            menuItemTitles: ["Share content"])
        let r = calibrationReport(ui, strings: s, source: "defaults", expectSharing: false)
        XCTAssertEqual(r.status(of: "meeting_window"), .fail)
        XCTAssertTrue(r.check("meeting_window")!.detail.contains("Zoom Workplace"))
        XCTAssertEqual(r.status(of: "start_share_item"), .fail)
        XCTAssertTrue(r.check("start_share_item")!.detail.contains("Share content"))
    }

    func testSharingDetection() {
        let sharing = ObservedUI(zoomRunning: true, windowTitles: ["Zoom Meeting", "zoom share toolbar window"],
                                 menuBarTitles: ["Meeting"], menuItemTitles: ["Stop Share"])
        XCTAssertEqual(calibrationReport(sharing, strings: s, source: "x", expectSharing: true).status, .ok)
        let notDetected = ObservedUI(zoomRunning: true, windowTitles: ["Zoom Meeting"], menuBarTitles: ["Meeting"],
                                     menuItemTitles: ["End Share"])
        XCTAssertEqual(calibrationReport(notDetected, strings: s, source: "x", expectSharing: true)
            .status(of: "sharing_detected"), .fail)
        XCTAssertEqual(calibrationReport(sharing, strings: s, source: "x", expectSharing: false)
            .status(of: "sharing_detected"), .fail, "false positive while not sharing must fail")
    }
}
