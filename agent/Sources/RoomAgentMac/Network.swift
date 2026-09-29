#if os(macOS)
import Foundation
import Network
import RoomAgentCore

public struct TCPReachability: NetworkProbe {
    public init() {}

    public func canReach(host: String, port: UInt16, timeout: Double) -> Bool {
        guard let p = NWEndpoint.Port(rawValue: port) else { return false }
        let conn = NWConnection(host: NWEndpoint.Host(host), port: p, using: .tcp)
        let done = DispatchSemaphore(value: 0)
        let lock = NSLock()
        var ready = false
        conn.stateUpdateHandler = { state in
            switch state {
            case .ready:
                lock.lock(); ready = true; lock.unlock()
                done.signal()
            case .failed, .cancelled:
                done.signal()
            default:
                break
            }
        }
        conn.start(queue: .global())
        _ = done.wait(timeout: .now() + timeout)
        conn.cancel()
        lock.lock(); defer { lock.unlock() }
        return ready
    }
}
#endif
