#if os(macOS)
import AppKit
import AVFoundation
import CoreGraphics
import Foundation
import RoomAgentCore

public struct MacDevices: DeviceCatalog, DisplayCatalog {
    public init() {}

    static let videoTypes: [AVCaptureDevice.DeviceType] = [.external, .builtInWideAngleCamera, .continuityCamera]

    static func videoCaptureDevices() -> [AVCaptureDevice] {
        AVCaptureDevice.DiscoverySession(deviceTypes: videoTypes, mediaType: .video, position: .unspecified).devices
    }

    static func audioCaptureDevices() -> [AVCaptureDevice] {
        AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external], mediaType: .audio,
                                         position: .unspecified).devices
    }

    public func videoDevices() -> [String] { Self.videoCaptureDevices().map(\.localizedName) }

    public func audioInputDevices() -> [String] { CoreAudioRouting.inputDevices().map(\.name) }

    public func displays() -> [DisplayInfo] {
        let main = CGMainDisplayID()
        return NSScreen.screens.enumerated().compactMap { idx, screen in
            guard let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            else { return nil }
            let id = CGDirectDisplayID(num.uint32Value)
            return DisplayInfo(id: id, name: screen.localizedName, index: idx + 1, isMain: id == main)
        }
    }
}

public struct MacSession: SessionProbe {
    public init() {}

    var info: [String: Any] { (CGSessionCopyCurrentDictionary() as? [String: Any]) ?? [:] }

    public func isScreenLocked() -> Bool { (info["CGSSessionScreenIsLocked"] as? Bool) ?? false }

    public func isOnConsole() -> Bool { (info[kCGSessionOnConsoleKey as String] as? Bool) ?? false }
}

/// Spins the run loop instead of blocking, so AppKit/AX notifications are delivered while we wait.
public struct RunLoopSleeper: Sleeper {
    public init() {}

    public func sleep(_ seconds: Double) {
        RunLoop.current.run(until: Date().addingTimeInterval(seconds))
    }
}
#endif
