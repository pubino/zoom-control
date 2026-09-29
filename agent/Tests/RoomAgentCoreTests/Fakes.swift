import Foundation
@testable import RoomAgentCore

final class FakeHost: DeviceCatalog, DisplayCatalog, MediaProbe, SessionProbe, NetworkProbe, ZoomApp, ZoomUI,
    AudioRouting, Sleeper {
    var video = ["FaceTime HD Camera", "Blackmagic UltraStudio Mini"]
    var audio = ["MacBook Pro Microphone", "Dante Virtual Soundcard"]
    var screens = [DisplayInfo(id: 1, name: "Built-in Display", index: 1, isMain: true),
                   DisplayInfo(id: 7, name: "Display 2", index: 2, isMain: false)]
    var videoSample = VideoSample(fps: 29.97, meanLuma: 0.4)
    var videoError: Error?
    var audioDB = -30.0
    var lumaByDisplay: [UInt32: Double] = [7: 0.5]
    var locked = false
    var console = true
    var unreachable: Set<String> = []
    var running = false
    var inMeeting = false
    var sharing = false
    var becomeRunningOnOpen = true
    var joinAfterPolls = 0
    var shareAfterPolls = 0
    var quitWorks = true
    var cameraSelectError: Error?
    var shareError: Error?

    var opened: [URL] = []
    var quits: [Bool] = []
    var sharedDisplays: [DisplayInfo] = []
    var defaultInput: String?
    var camera: String?
    var slept = 0.0

    func videoDevices() -> [String] { video }
    func audioInputDevices() -> [String] { audio }
    func displays() -> [DisplayInfo] { screens }
    func measureVideo(device: String, seconds: Double) throws -> VideoSample {
        if let e = videoError { throw e }
        return videoSample
    }
    func measureAudio(device: String, seconds: Double) throws -> Double { audioDB }
    func displayLuma(displayID: UInt32) throws -> Double {
        guard let l = lumaByDisplay[displayID] else { throw NSError(domain: "sck", code: 1) }
        return l
    }
    func isScreenLocked() -> Bool { locked }
    func isOnConsole() -> Bool { console }
    func canReach(host: String, port: UInt16, timeout: Double) -> Bool { !unreachable.contains(host) }
    func isRunning() -> Bool { running }
    func open(_ url: URL, mode: LaunchMode) throws {
        opened.append(url)
        if becomeRunningOnOpen { running = true }
    }
    func quit(force: Bool, timeout: Double) -> Bool {
        quits.append(force)
        if quitWorks || force { running = false; inMeeting = false; sharing = false; return true }
        return false
    }
    func isInMeeting() -> Bool {
        if running && !inMeeting && joinAfterPolls >= 0 {
            if joinAfterPolls == 0 { inMeeting = true } else { joinAfterPolls -= 1 }
        }
        return inMeeting
    }
    func isSharing() -> Bool {
        if !sharedDisplays.isEmpty && !sharing && shareAfterPolls >= 0 {
            if shareAfterPolls == 0 { sharing = true } else { shareAfterPolls -= 1 }
        }
        return sharing
    }
    func startShare(display: DisplayInfo) throws -> ShareMethod {
        if let e = shareError { throw e }
        sharedDisplays.append(display)
        return .accessibility
    }
    func selectCamera(named: String) throws {
        if let e = cameraSelectError { throw e }
        camera = named
    }
    func setDefaultInput(named: String) throws { defaultInput = named }
    func sleep(_ seconds: Double) { slept += seconds }

    var env: AgentEnvironment {
        AgentEnvironment(devices: self, displays: self, media: self, session: self, network: self, zoom: self,
                         ui: self, audio: self, sleeper: self)
    }
}

let room = RoomConfig(videoDevice: "Blackmagic UltraStudio Mini", audioDevice: "Dante Virtual Soundcard",
                      display: "Display 2", minFPS: 1, minDBFS: -70, blackLuma: 0.02)

extension Report {
    func status(of name: String) -> Status? { check(name)?.status }
}
