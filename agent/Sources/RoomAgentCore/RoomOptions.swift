import Foundation

/// Room routing and thresholds, passed as flags by zoomctl (mirrors Room.spec in YAML).
public struct RoomConfig: Equatable, Sendable {
    public var videoDevice: String
    public var audioDevice: String
    public var display: String
    public var minFPS: Double
    public var minDBFS: Double
    public var blackLuma: Double

    public init(videoDevice: String, audioDevice: String, display: String,
                minFPS: Double = 1, minDBFS: Double = -70, blackLuma: Double = 0.02) {
        self.videoDevice = videoDevice
        self.audioDevice = audioDevice
        self.display = display
        self.minFPS = minFPS
        self.minDBFS = minDBFS
        self.blackLuma = blackLuma
    }
}

public enum Match: Equatable, Sendable {
    case found(String)
    case missing
    case ambiguous([String])
}

/// Resolve a configured device/display name against what the OS reports:
/// case-insensitive exact match wins; otherwise a unique substring match.
public func matchName(_ wanted: String, in available: [String]) -> Match {
    let w = wanted.lowercased()
    if let exact = available.first(where: { $0.lowercased() == w }) { return .found(exact) }
    let partial = available.filter { $0.lowercased().contains(w) }
    switch partial.count {
    case 0: return .missing
    case 1: return .found(partial[0])
    default: return .ambiguous(partial)
    }
}

public func describe(_ m: Match, kind: String, wanted: String, available: [String]) -> Check {
    switch m {
    case .found(let name): return Check(kind, .ok, name)
    case .missing: return Check(kind, .fail, "\(wanted) not found; available: \(available.joined(separator: ", "))")
    case .ambiguous(let names): return Check(kind, .fail, "\(wanted) is ambiguous: \(names.joined(separator: ", "))")
    }
}
