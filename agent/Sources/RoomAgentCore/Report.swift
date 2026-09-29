import Foundation

public let roomAgentVersion = "0.1.0"

public enum Status: String, Codable, Comparable, Sendable {
    case ok, warn, fail

    private var rank: Int { self == .ok ? 0 : self == .warn ? 1 : 2 }
    public static func < (lhs: Status, rhs: Status) -> Bool { lhs.rank < rhs.rank }
}

public struct Check: Codable, Equatable, Sendable {
    public var name: String
    public var status: Status
    public var detail: String

    public init(_ name: String, _ status: Status, _ detail: String = "") {
        self.name = name
        self.status = status
        self.detail = detail
    }
}

/// The single JSON document every roomagent command prints. Consumers treat a
/// missing or unparseable report as failure, so every code path must emit one.
public struct Report: Codable, Equatable, Sendable {
    public var command: String
    public var status: Status
    public var checks: [Check]
    public var error: String?
    public var version: String

    public init(command: String, checks: [Check], error: String? = nil) {
        self.command = command
        self.checks = checks
        self.error = error
        self.version = roomAgentVersion
        let worst = checks.map(\.status).max() ?? .ok
        self.status = error == nil ? worst : .fail
    }

    public func check(_ name: String) -> Check? { checks.first { $0.name == name } }

    /// 0 for ok/warn, 2 for fail (1 and 64+ are reserved for crashes/usage errors).
    public var exitCode: Int32 { status == .fail ? 2 : 0 }

    public func json(pretty: Bool = false) -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys] : [.sortedKeys]
        guard let data = try? enc.encode(self), let s = String(data: data, encoding: .utf8) else {
            return #"{"command":"\#(command)","status":"fail","checks":[],"error":"report encoding failed"}"#
        }
        return s
    }
}
