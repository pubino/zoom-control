import Foundation

public struct DisplayInfo: Equatable, Sendable {
    public var id: UInt32
    public var name: String
    /// 1-based position in the OS display order ("Screen 1", "Screen 2", … in Zoom's picker).
    public var index: Int
    public var isMain: Bool

    public init(id: UInt32, name: String, index: Int, isMain: Bool) {
        self.id = id
        self.name = name
        self.index = index
        self.isMain = isMain
    }
}

public struct VideoSample: Equatable, Sendable {
    public var fps: Double
    public var meanLuma: Double

    public init(fps: Double, meanLuma: Double) {
        self.fps = fps
        self.meanLuma = meanLuma
    }
}

public protocol DeviceCatalog {
    func videoDevices() -> [String]
    func audioInputDevices() -> [String]
}

public protocol DisplayCatalog {
    func displays() -> [DisplayInfo]
}

public protocol MediaProbe {
    func measureVideo(device: String, seconds: Double) throws -> VideoSample
    /// RMS level in dBFS (−.infinity for digital silence).
    func measureAudio(device: String, seconds: Double) throws -> Double
    /// Mean luma 0…1 of what the display is currently showing.
    func displayLuma(displayID: UInt32) throws -> Double
}

public protocol SessionProbe {
    func isScreenLocked() -> Bool
    func isOnConsole() -> Bool
}

public protocol NetworkProbe {
    func canReach(host: String, port: UInt16, timeout: Double) -> Bool
}

public protocol ZoomApp {
    func isRunning() -> Bool
    func open(_ url: URL, mode: LaunchMode) throws
    /// Returns true once no Zoom process remains.
    func quit(force: Bool, timeout: Double) -> Bool
}

public enum ShareMethod: String, Sendable {
    case accessibility, keystroke, alreadySharing
}

public protocol ZoomUI {
    func isInMeeting() -> Bool
    func isSharing() -> Bool
    func startShare(display: DisplayInfo) throws -> ShareMethod
    func selectCamera(named: String) throws
}

public protocol AudioRouting {
    func setDefaultInput(named: String) throws
}

public protocol Sleeper {
    func sleep(_ seconds: Double)
}

/// Everything a command may touch on the host, injected so logic is testable with fakes.
public struct AgentEnvironment {
    public var devices: DeviceCatalog
    public var displays: DisplayCatalog
    public var media: MediaProbe
    public var session: SessionProbe
    public var network: NetworkProbe
    public var zoom: ZoomApp
    public var ui: ZoomUI
    public var audio: AudioRouting
    public var sleeper: Sleeper

    public init(devices: DeviceCatalog, displays: DisplayCatalog, media: MediaProbe, session: SessionProbe,
                network: NetworkProbe, zoom: ZoomApp, ui: ZoomUI, audio: AudioRouting, sleeper: Sleeper) {
        self.devices = devices
        self.displays = displays
        self.media = media
        self.session = session
        self.network = network
        self.zoom = zoom
        self.ui = ui
        self.audio = audio
        self.sleeper = sleeper
    }
}
