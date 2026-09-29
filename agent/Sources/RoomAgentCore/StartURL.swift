import Foundation

public enum LaunchMode: String, Sendable, CaseIterable {
    /// Open `zoommtg://…/start?confno=…&zak=…` directly in the Zoom client (no browser hop).
    case zoommtg
    /// Hand the https start_url to zoom.us.app (fallback if the scheme changes).
    case https
}

public enum StartURLError: Error, Equatable, CustomStringConvertible {
    case invalid(String)

    public var description: String {
        switch self { case .invalid(let why): return "invalid start_url: \(why)" }
    }
}

/// Convert Zoom's `https://<host>/s/<id>?zak=…` start_url into the client URL scheme so
/// macOS never shows a browser "Open zoom.us?" prompt on an unattended machine.
public func zoomClientURL(from startURL: String) throws -> URL {
    guard let comps = URLComponents(string: startURL.trimmingCharacters(in: .whitespacesAndNewlines)),
          comps.scheme == "https", let host = comps.host, host.hasSuffix("zoom.us") else {
        throw StartURLError.invalid("expected https://*.zoom.us/s/<id>?zak=…")
    }
    let parts = comps.path.split(separator: "/")
    guard parts.count == 2, parts[0] == "s", !parts[1].isEmpty, parts[1].allSatisfy(\.isNumber) else {
        throw StartURLError.invalid("path is not /s/<numeric id>")
    }
    let items = comps.queryItems ?? []
    guard items.contains(where: { $0.name == "zak" && !($0.value ?? "").isEmpty }) else {
        throw StartURLError.invalid("missing zak token")
    }
    var out = URLComponents()
    out.scheme = "zoommtg"
    out.host = host
    out.path = "/start"
    out.queryItems = [URLQueryItem(name: "action", value: "start"), URLQueryItem(name: "confno", value: String(parts[1]))]
        + items.filter { $0.name != "confno" && $0.name != "action" }
    guard let url = out.url else { throw StartURLError.invalid("could not build client URL") }
    return url
}

public func launchURL(from startURL: String, mode: LaunchMode) throws -> URL {
    let client = try zoomClientURL(from: startURL)  // validates in both modes
    switch mode {
    case .zoommtg:
        return client
    case .https:
        guard let url = URL(string: startURL.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw StartURLError.invalid("unparseable")
        }
        return url
    }
}

/// Redact secrets (zak, pwd, tk) for logging.
public func redact(_ url: String) -> String {
    guard var comps = URLComponents(string: url) else { return "<unparseable url>" }
    comps.queryItems = comps.queryItems?.map {
        ["zak", "pwd", "tk"].contains($0.name) ? URLQueryItem(name: $0.name, value: "REDACTED") : $0
    }
    return comps.string ?? "<unparseable url>"
}
