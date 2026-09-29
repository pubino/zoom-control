import Foundation

/// Zoom's Accessibility strings. Zoom renames these between releases, so they can be
/// overridden per node with a JSON file (any subset of keys) — no rebuild or re-sign needed.
public struct ZoomUIStrings: Codable, Equatable, Sendable {
    public var meetingWindowTitles: [String]
    public var meetingMenu: String
    public var startShareItems: [String]
    public var stopShareItems: [String]
    public var shareToolbarWindows: [String]
    public var sharePickerButtons: [String]
    /// Share-picker tile labels; "{n}" is the display's 1-based index, "{name}" its name.
    public var sharePickerTiles: [String]

    public static let defaults = ZoomUIStrings(
        meetingWindowTitles: ["Zoom Meeting", "Zoom Webinar", "Zoom Workplace Meeting"],
        meetingMenu: "Meeting",
        startShareItems: ["Share Screen", "Start Share", "Share screen"],
        stopShareItems: ["Stop Share", "Stop share", "Stop Sharing"],
        shareToolbarWindows: ["zoom share toolbar window", "zoom share statusbar window"],
        sharePickerButtons: ["Share", "Share Screen"],
        sharePickerTiles: ["Screen {n}", "Desktop {n}", "{name}"]
    )

    public init(meetingWindowTitles: [String], meetingMenu: String, startShareItems: [String],
                stopShareItems: [String], shareToolbarWindows: [String], sharePickerButtons: [String],
                sharePickerTiles: [String]) {
        self.meetingWindowTitles = meetingWindowTitles
        self.meetingMenu = meetingMenu
        self.startShareItems = startShareItems
        self.stopShareItems = stopShareItems
        self.shareToolbarWindows = shareToolbarWindows
        self.sharePickerButtons = sharePickerButtons
        self.sharePickerTiles = sharePickerTiles
    }

    private struct Partial: Decodable {
        var meetingWindowTitles: [String]?
        var meetingMenu: String?
        var startShareItems: [String]?
        var stopShareItems: [String]?
        var shareToolbarWindows: [String]?
        var sharePickerButtons: [String]?
        var sharePickerTiles: [String]?
    }

    /// Defaults overlaid with whichever keys the JSON provides. Unknown keys are an error
    /// (a typo must not silently fall back to defaults).
    public static func merged(json: Data) throws -> ZoomUIStrings {
        let obj = try JSONSerialization.jsonObject(with: json)
        guard let dict = obj as? [String: Any] else { throw UIStringsError.invalid("top level must be an object") }
        let known = Set(["meetingWindowTitles", "meetingMenu", "startShareItems", "stopShareItems",
                         "shareToolbarWindows", "sharePickerButtons", "sharePickerTiles"])
        let unknown = Set(dict.keys).subtracting(known)
        guard unknown.isEmpty else { throw UIStringsError.invalid("unknown keys: \(unknown.sorted().joined(separator: ", "))") }
        let p: Partial
        do { p = try JSONDecoder().decode(Partial.self, from: json) } catch {
            throw UIStringsError.invalid("\(error)")
        }
        var s = defaults
        if let v = p.meetingWindowTitles { s.meetingWindowTitles = v }
        if let v = p.meetingMenu { s.meetingMenu = v }
        if let v = p.startShareItems { s.startShareItems = v }
        if let v = p.stopShareItems { s.stopShareItems = v }
        if let v = p.shareToolbarWindows { s.shareToolbarWindows = v }
        if let v = p.sharePickerButtons { s.sharePickerButtons = v }
        if let v = p.sharePickerTiles { s.sharePickerTiles = v }
        return s
    }

    public func tiles(for display: DisplayInfo) -> [String] {
        sharePickerTiles.map {
            $0.replacingOccurrences(of: "{n}", with: String(display.index)).replacingOccurrences(of: "{name}", with: display.name)
        }
    }

    public static let systemPath = "/Library/Application Support/zoom-control/ui-strings.json"

    public static func candidatePaths(env: [String: String], home: String) -> [String] {
        if let explicit = env["ROOMAGENT_UI_STRINGS"], !explicit.isEmpty { return [explicit] }
        return [home + "/Library/Application Support/zoom-control/ui-strings.json", systemPath]
    }

    /// Returns effective strings and where they came from ("defaults" or a path).
    /// An explicitly named file that is missing, or any unreadable/invalid file, throws.
    public static func load(env: [String: String] = ProcessInfo.processInfo.environment,
                            home: String = NSHomeDirectory(),
                            fileExists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) },
                            read: (String) throws -> Data = { try Data(contentsOf: URL(fileURLWithPath: $0)) })
        throws -> (ZoomUIStrings, String) {
        let paths = candidatePaths(env: env, home: home)
        let explicit = env["ROOMAGENT_UI_STRINGS"].map { !$0.isEmpty } ?? false
        for path in paths {
            guard fileExists(path) else {
                if explicit { throw UIStringsError.invalid("ROOMAGENT_UI_STRINGS=\(path) does not exist") }
                continue
            }
            do { return (try merged(json: read(path)), path) } catch UIStringsError.invalid(let why) {
                throw UIStringsError.invalid("\(path): \(why)")
            } catch {
                throw UIStringsError.invalid("\(path): \(error)")
            }
        }
        return (defaults, "defaults")
    }
}

public enum UIStringsError: Error, CustomStringConvertible, Equatable {
    case invalid(String)
    public var description: String { if case .invalid(let s) = self { return "invalid UI strings: \(s)" }; return "" }
}

/// What the Accessibility tree currently shows (collected by the Mac adapter).
public struct ObservedUI: Equatable, Sendable {
    public var zoomRunning: Bool
    public var windowTitles: [String]
    public var menuBarTitles: [String]
    public var menuItemTitles: [String]

    public init(zoomRunning: Bool, windowTitles: [String], menuBarTitles: [String], menuItemTitles: [String]) {
        self.zoomRunning = zoomRunning
        self.windowTitles = windowTitles
        self.menuBarTitles = menuBarTitles
        self.menuItemTitles = menuItemTitles
    }
}

/// Compare observed UI against the configured strings. Run it during a test meeting,
/// once while not sharing and once while sharing (`expectSharing`).
public func calibrationReport(_ ui: ObservedUI, strings s: ZoomUIStrings, source: String, expectSharing: Bool) -> Report {
    var checks = [Check("ui_strings_source", .ok, source)]
    guard ui.zoomRunning else {
        return Report(command: "calibrate", checks: checks + [Check("zoom_running", .fail, "start a test meeting first")])
    }
    let seenWindows = ui.windowTitles.joined(separator: " | ")
    if let w = ui.windowTitles.first(where: { t in s.meetingWindowTitles.contains { t.hasPrefix($0) } }) {
        checks.append(Check("meeting_window", .ok, w))
    } else if ui.menuBarTitles.contains(s.meetingMenu) {
        checks.append(Check("meeting_window", .warn, "detected only via '\(s.meetingMenu)' menu; windows: \(seenWindows)"))
    } else {
        checks.append(Check("meeting_window", .fail,
                            "no window matches meetingWindowTitles and no '\(s.meetingMenu)' menu; windows: \(seenWindows); menus: \(ui.menuBarTitles.joined(separator: " | "))"))
    }
    checks.append(ui.menuBarTitles.contains(s.meetingMenu)
                  ? Check("meeting_menu", .ok, s.meetingMenu)
                  : Check("meeting_menu", .warn, "menus: \(ui.menuBarTitles.joined(separator: " | "))"))

    let items = Set(ui.menuItemTitles)
    let start = s.startShareItems.first(where: items.contains)
    let stop = s.stopShareItems.first(where: items.contains)
    let toolbar = ui.windowTitles.first { s.shareToolbarWindows.contains($0.lowercased()) }
    let shareLike = ui.menuItemTitles.filter { $0.localizedCaseInsensitiveContains("share") }.joined(separator: " | ")
    if expectSharing {
        if stop != nil || toolbar != nil {
            checks.append(Check("sharing_detected", .ok, stop ?? toolbar ?? ""))
        } else {
            checks.append(Check("sharing_detected", .fail, "neither stopShareItems nor shareToolbarWindows matched; share-like items: \(shareLike); windows: \(seenWindows)"))
        }
    } else {
        checks.append(start != nil ? Check("start_share_item", .ok, start!)
                                   : Check("start_share_item", .fail, "no startShareItems matched (keystroke fallback only); share-like items: \(shareLike)"))
        if stop != nil || toolbar != nil {
            checks.append(Check("sharing_detected", .fail, "reports sharing while not sharing: \(stop ?? toolbar ?? "")"))
        }
    }
    return Report(command: "calibrate", checks: checks)
}
