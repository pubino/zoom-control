#if os(macOS)
import CoreAudio
import Foundation
import RoomAgentCore

enum CoreAudioError: Error, CustomStringConvertible {
    case status(String, OSStatus)
    case notFound(String)

    var description: String {
        switch self {
        case .status(let op, let s): return "\(op) failed (OSStatus \(s))"
        case .notFound(let n): return "audio device \(n) not found"
        }
    }
}

/// CoreAudio device enumeration and default-input routing (Zoom's mic is "Same as System").
public struct CoreAudioRouting: AudioRouting {
    public init() {}

    static func address(_ selector: AudioObjectPropertySelector,
                        _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    static func allDevices() -> [AudioDeviceID] {
        var addr = address(kAudioHardwarePropertyDevices)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr
        else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr
        else { return [] }
        return ids
    }

    static func name(of id: AudioDeviceID) -> String? {
        var addr = address(kAudioObjectPropertyName)
        var name: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &name) == noErr, let n = name else { return nil }
        return n.takeRetainedValue() as String
    }

    static func inputChannels(of id: AudioDeviceID) -> Int {
        var addr = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
        let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        return list.reduce(0) { $0 + Int($1.mNumberChannels) }
    }

    public static func inputDevices() -> [(id: AudioDeviceID, name: String)] {
        allDevices().compactMap { id in
            guard inputChannels(of: id) > 0, let n = name(of: id) else { return nil }
            return (id, n)
        }
    }

    public func setDefaultInput(named: String) throws {
        guard let dev = Self.inputDevices().first(where: { $0.name == named }) else { throw CoreAudioError.notFound(named) }
        var addr = Self.address(kAudioHardwarePropertyDefaultInputDevice)
        var id = dev.id
        let st = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil,
                                            UInt32(MemoryLayout<AudioDeviceID>.size), &id)
        guard st == noErr else { throw CoreAudioError.status("set default input", st) }
    }
}
#endif
