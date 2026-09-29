import XCTest
@testable import RoomAgentCore

final class MatchTests: XCTestCase {
    func testExactCaseInsensitiveBeatsPartial() {
        XCTAssertEqual(matchName("display 2", in: ["Display 2", "Display 20"]), .found("Display 2"))
    }

    func testUniquePartial() {
        XCTAssertEqual(matchName("UltraStudio", in: ["FaceTime", "Blackmagic UltraStudio Mini"]),
                       .found("Blackmagic UltraStudio Mini"))
    }

    func testAmbiguousAndMissing() {
        XCTAssertEqual(matchName("Dell", in: ["DELL U2720Q (1)", "DELL U2720Q (2)"]),
                       .ambiguous(["DELL U2720Q (1)", "DELL U2720Q (2)"]))
        XCTAssertEqual(matchName("Nope", in: ["A"]), .missing)
    }
}

final class StartURLTests: XCTestCase {
    func testConvertsToClientScheme() throws {
        let url = try zoomClientURL(from: "https://us06web.zoom.us/s/81234567890?zak=eyJabc&pwd=xyz\n")
        XCTAssertEqual(url.scheme, "zoommtg")
        XCTAssertEqual(url.host, "us06web.zoom.us")
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        XCTAssertEqual(items.first { $0.name == "confno" }?.value, "81234567890")
        XCTAssertEqual(items.first { $0.name == "zak" }?.value, "eyJabc")
        XCTAssertEqual(items.first { $0.name == "pwd" }?.value, "xyz")
        XCTAssertEqual(items.first { $0.name == "action" }?.value, "start")
    }

    func testRejectsBadURLs() {
        for bad in ["http://zoom.us/s/1?zak=a", "https://evil.example/s/1?zak=a", "https://zoom.us/j/1?zak=a",
                    "https://zoom.us/s/abc?zak=a", "https://zoom.us/s/1", "https://zoom.us/s/1?zak="] {
            XCTAssertThrowsError(try zoomClientURL(from: bad), bad)
        }
    }

    func testHttpsModeValidatesToo() throws {
        XCTAssertEqual(try launchURL(from: "https://zoom.us/s/1?zak=a", mode: .https).scheme, "https")
        XCTAssertThrowsError(try launchURL(from: "https://zoom.us/x", mode: .https))
    }

    func testRedaction() {
        let r = redact("zoommtg://zoom.us/start?confno=1&zak=SECRET&pwd=P")
        XCTAssertFalse(r.contains("SECRET"))
        XCTAssertFalse(r.contains("=P"))
        XCTAssertTrue(r.contains("confno=1"))
    }
}

final class ReportTests: XCTestCase {
    func testStatusIsWorstCheckAndErrorForcesFail() {
        XCTAssertEqual(Report(command: "x", checks: [Check("a", .ok), Check("b", .warn)]).status, .warn)
        XCTAssertEqual(Report(command: "x", checks: [Check("a", .ok), Check("b", .fail)]).exitCode, 2)
        XCTAssertEqual(Report(command: "x", checks: [], error: "boom").status, .fail)
    }

    func testJSONContract() throws {
        let r = Report(command: "health", checks: [Check("sharing", .fail, "lost")])
        let obj = try JSONSerialization.jsonObject(with: Data(r.json().utf8)) as! [String: Any]
        XCTAssertEqual(obj["status"] as? String, "fail")
        XCTAssertEqual(obj["command"] as? String, "health")
        let checks = obj["checks"] as! [[String: Any]]
        XCTAssertEqual(checks.first?["name"] as? String, "sharing")
    }
}

final class PreflightTests: XCTestCase {
    func testAllGood() {
        let host = FakeHost()
        let r = Commands(env: host.env).preflight(room)
        XCTAssertEqual(r.status, .ok, r.json(pretty: true))
        XCTAssertEqual(r.check("display_present")?.detail, "Display 2 (#2, id 7)")
    }

    func testMissingCaptureCardFails() {
        let host = FakeHost()
        host.video = ["FaceTime HD Camera"]
        let r = Commands(env: host.env).preflight(room)
        XCTAssertEqual(r.status, .fail)
        XCTAssertEqual(r.status(of: "video_device"), .fail)
        XCTAssertTrue(r.check("video_device")!.detail.contains("available: FaceTime HD Camera"))
        XCTAssertEqual(r.status(of: "video_signal"), .fail)
    }

    func testSilentRoomAndDarkScreenOnlyWarn() {
        let host = FakeHost()
        host.audioDB = -.infinity
        host.lumaByDisplay[7] = 0.0
        let r = Commands(env: host.env).preflight(room)
        XCTAssertEqual(r.status, .warn)
        XCTAssertEqual(r.check("audio_signal")?.detail.contains("digital silence"), true)
    }

    func testZeroFramesFails() {
        let host = FakeHost()
        host.videoSample = VideoSample(fps: 0, meanLuma: 0)
        XCTAssertEqual(Commands(env: host.env).preflight(room).status(of: "video_signal"), .fail)
    }

    func testLockedScreenAndNetworkFail() {
        let host = FakeHost()
        host.locked = true
        host.unreachable = ["api.zoom.us"]
        let r = Commands(env: host.env).preflight(room)
        XCTAssertEqual(r.status(of: "screen_unlocked"), .fail)
        XCTAssertEqual(r.status(of: "network_api.zoom.us"), .fail)
        XCTAssertEqual(r.status(of: "network_zoom.us"), .ok)
    }

    func testStaleZoomIsQuitButLiveMeetingIsNot() {
        let stale = FakeHost()
        stale.running = true
        stale.joinAfterPolls = -1  // never in meeting
        let r1 = Commands(env: stale.env).preflight(room)
        XCTAssertEqual(r1.status(of: "zoom_idle"), .warn)
        XCTAssertEqual(stale.quits, [false])

        let live = FakeHost()
        live.running = true
        live.inMeeting = true
        let r2 = Commands(env: live.env).preflight(room)
        XCTAssertEqual(r2.status(of: "zoom_idle"), .fail)
        XCTAssertEqual(live.quits, [])
    }
}

final class LaunchShareTests: XCTestCase {
    func testLaunchOpensClientURLAndWaitsForMeeting() {
        let host = FakeHost()
        host.joinAfterPolls = 3
        let r = Commands(env: host.env).launch(startURL: "https://zoom.us/s/42?zak=Z", mode: .zoommtg, timeout: 60)
        XCTAssertEqual(r.status, .ok, r.json())
        XCTAssertEqual(host.opened.first?.scheme, "zoommtg")
        XCTAssertFalse(r.json().contains("zak=Z"), "token must be redacted in output")
        XCTAssertEqual(host.slept, 3)
    }

    func testLaunchTimesOutWithoutMeeting() {
        let host = FakeHost()
        host.joinAfterPolls = -1
        let r = Commands(env: host.env).launch(startURL: "https://zoom.us/s/42?zak=Z", mode: .zoommtg, timeout: 5)
        XCTAssertEqual(r.status(of: "in_meeting"), .fail)
    }

    func testLaunchRejectsBadURLWithoutOpening() {
        let host = FakeHost()
        let r = Commands(env: host.env).launch(startURL: "https://zoom.us/j/42", mode: .zoommtg, timeout: 5)
        XCTAssertEqual(r.status(of: "start_url"), .fail)
        XCTAssertTrue(host.opened.isEmpty)
    }

    func testShareTargetsConfiguredDisplayAndVerifies() {
        let host = FakeHost()
        host.running = true
        host.inMeeting = true
        host.shareAfterPolls = 2
        let r = Commands(env: host.env).share(room, timeout: 10)
        XCTAssertEqual(r.status, .ok, r.json())
        XCTAssertEqual(host.sharedDisplays.map(\.id), [7])
    }

    func testShareUnverifiedFails() {
        let host = FakeHost()
        host.running = true
        host.inMeeting = true
        host.shareAfterPolls = -1
        let r = Commands(env: host.env).share(room, timeout: 3)
        XCTAssertEqual(r.status(of: "sharing"), .fail)
        XCTAssertTrue(r.check("sharing")!.detail.contains("not active"))
    }

    func testShareNotInMeeting() {
        let host = FakeHost()
        XCTAssertEqual(Commands(env: host.env).share(room, timeout: 3).status(of: "sharing"), .fail)
        XCTAssertTrue(host.sharedDisplays.isEmpty)
    }

    func testAlreadySharingIsIdempotent() {
        let host = FakeHost()
        host.running = true
        host.inMeeting = true
        host.sharing = true
        let r = Commands(env: host.env).share(room, timeout: 3)
        XCTAssertEqual(r.check("sharing")?.detail, "alreadySharing")
        XCTAssertTrue(host.sharedDisplays.isEmpty)
    }
}

final class SelectAVHealthQuitTests: XCTestCase {
    func testSelectAVRoutesBoth() {
        let host = FakeHost()
        let r = Commands(env: host.env).selectAV(room)
        XCTAssertEqual(r.status, .ok)
        XCTAssertEqual(host.defaultInput, "Dante Virtual Soundcard")
        XCTAssertEqual(host.camera, "Blackmagic UltraStudio Mini")
    }

    func testCameraSelectionFailureWarns() {
        let host = FakeHost()
        host.cameraSelectError = NSError(domain: "ax", code: 2)
        XCTAssertEqual(Commands(env: host.env).selectAV(room).status, .warn)
    }

    func testMissingAudioDeviceFails() {
        let host = FakeHost()
        host.audio = []
        XCTAssertEqual(Commands(env: host.env).selectAV(room).status(of: "audio_route"), .fail)
        XCTAssertNil(host.defaultInput)
    }

    func testHealthyLive() {
        let host = FakeHost()
        host.running = true
        host.inMeeting = true
        host.sharing = true
        let r = Commands(env: host.env).health(room)
        XCTAssertEqual(r.status, .ok, r.json(pretty: true))
        XCTAssertEqual(Set(r.checks.map(\.name)), ["zoom_running", "in_meeting", "sharing", "screen_unlocked",
                                                   "display_present", "display_not_black", "video_signal",
                                                   "audio_signal"])
    }

    func testHealthCrashAndSilenceAreFailures() {
        let host = FakeHost()
        host.audioDB = -90
        host.lumaByDisplay[7] = 0.01
        let r = Commands(env: host.env).health(room)
        for name in ["zoom_running", "in_meeting", "sharing", "audio_signal", "display_not_black"] {
            XCTAssertEqual(r.status(of: name), .fail, name)
        }
    }

    func testCaptureErrorIsFailureNotSilence() {
        let host = FakeHost()
        host.videoError = NSError(domain: "av", code: -11800)
        XCTAssertEqual(Commands(env: host.env).health(room).status(of: "video_signal"), .fail)
    }

    func testQuit() {
        let host = FakeHost()
        XCTAssertEqual(Commands(env: host.env).quit(force: false).status, .ok)
        XCTAssertEqual(host.quits, [])
        host.running = true
        host.quitWorks = false
        XCTAssertEqual(Commands(env: host.env).quit(force: false).status, .fail)
        XCTAssertEqual(Commands(env: host.env).quit(force: true).status, .ok)
    }
}
