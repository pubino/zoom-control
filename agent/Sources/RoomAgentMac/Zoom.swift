#if os(macOS)
import AppKit
import ApplicationServices
import Foundation
import RoomAgentCore

public let zoomBundleID = "us.zoom.xos"

enum ZoomError: Error, CustomStringConvertible {
    case notInstalled
    case openFailed(String)
    case accessibilityDenied
    case notRunning
    case elementNotFound(String)

    var description: String {
        switch self {
        case .notInstalled: return "zoom.us.app is not installed"
        case .openFailed(let why): return "could not open start URL: \(why)"
        case .accessibilityDenied: return "Accessibility access not granted to roomagent (see docs/security.md)"
        case .notRunning: return "zoom.us is not running"
        case .elementNotFound(let what): return "UI element not found: \(what) (calibrate with `roomagent ax-dump`)"
        }
    }
}

public struct MacZoomApp: ZoomApp {
    let sleeper = RunLoopSleeper()

    public init() {}

    static func apps() -> [NSRunningApplication] {
        NSRunningApplication.runningApplications(withBundleIdentifier: zoomBundleID).filter { !$0.isTerminated }
    }

    public func isRunning() -> Bool { !Self.apps().isEmpty }

    public func open(_ url: URL, mode: LaunchMode) throws {
        guard let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: zoomBundleID) else {
            throw ZoomError.notInstalled
        }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = true
        let sem = DispatchSemaphore(value: 0)
        var failure: Error?
        NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: cfg) { _, err in
            failure = err
            sem.signal()
        }
        let deadline = Date().addingTimeInterval(20)
        while sem.wait(timeout: .now()) == .timedOut {
            if Date() > deadline { throw ZoomError.openFailed("timed out") }
            sleeper.sleep(0.1)
        }
        if let failure { throw ZoomError.openFailed("\(failure)") }
    }

    public func quit(force: Bool, timeout: Double) -> Bool {
        for app in Self.apps() { _ = force ? app.forceTerminate() : app.terminate() }
        var waited = 0.0
        while isRunning() && waited < timeout {
            sleeper.sleep(0.5)
            waited += 0.5
        }
        return !isRunning()
    }
}

// MARK: - Accessibility helpers

struct AX {
    let element: AXUIElement

    func attr<T>(_ name: String) -> T? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value as? T
    }

    var role: String { attr(kAXRoleAttribute) ?? "" }
    var title: String { attr(kAXTitleAttribute) ?? "" }
    var desc: String { attr(kAXDescriptionAttribute) ?? "" }
    var label: String { [title, desc, attr(kAXValueAttribute) as String? ?? ""].first { !$0.isEmpty } ?? "" }
    var children: [AX] { (attr(kAXChildrenAttribute) as [AXUIElement]? ?? []).map(AX.init) }
    var windows: [AX] { (attr(kAXWindowsAttribute) as [AXUIElement]? ?? []).map(AX.init) }
    var menuBar: AX? { (attr(kAXMenuBarAttribute) as AXUIElement?).map(AX.init) }
    var enabled: Bool { attr(kAXEnabledAttribute) ?? true }

    @discardableResult
    func press() -> Bool { AXUIElementPerformAction(element, kAXPressAction as CFString) == .success }

    /// Depth-first search, bounded so a pathological tree can't hang the agent.
    func find(depth: Int = 12, _ pred: (AX) -> Bool) -> AX? {
        if pred(self) { return self }
        guard depth > 0 else { return nil }
        for c in children { if let hit = c.find(depth: depth - 1, pred) { return hit } }
        return nil
    }

    func dump(depth: Int) -> [String: Any] {
        var d: [String: Any] = ["role": role]
        if !title.isEmpty { d["title"] = title }
        if !desc.isEmpty { d["description"] = desc }
        if depth > 0 {
            let kids = children.map { $0.dump(depth: depth - 1) }
            if !kids.isEmpty { d["children"] = kids }
        }
        return d
    }
}

public struct MacZoomUI: ZoomUI {
    let sleeper = RunLoopSleeper()
    public let strings: ZoomUIStrings

    public init(strings: ZoomUIStrings = .defaults) { self.strings = strings }

    func app() throws -> AX {
        guard AXIsProcessTrusted() else { throw ZoomError.accessibilityDenied }
        guard let pid = MacZoomApp.apps().first?.processIdentifier else { throw ZoomError.notRunning }
        return AX(element: AXUIElementCreateApplication(pid))
    }

    func menuItem(_ app: AX, titles: [String]) -> AX? {
        app.menuBar?.find(depth: 4) { $0.role == kAXMenuItemRole && titles.contains($0.title) }
    }

    public func isInMeeting() -> Bool {
        guard let app = try? app() else { return false }
        if app.windows.contains(where: { w in strings.meetingWindowTitles.contains { w.title.hasPrefix($0) } }) {
            return true
        }
        return app.menuBar?.children.contains { $0.title == strings.meetingMenu } ?? false
    }

    public func isSharing() -> Bool {
        guard let app = try? app() else { return false }
        if app.windows.contains(where: { strings.shareToolbarWindows.contains($0.title.lowercased()) }) {
            return true
        }
        return menuItem(app, titles: strings.stopShareItems) != nil
    }

    public func startShare(display: DisplayInfo) throws -> ShareMethod {
        let app = try app()
        NSRunningApplication.runningApplications(withBundleIdentifier: zoomBundleID).first?.activate()
        var method = ShareMethod.accessibility
        if let item = menuItem(app, titles: strings.startShareItems), item.enabled {
            item.press()
        } else {
            method = .keystroke
            Keyboard.send(keyCode: 1 /* s */, flags: [.maskCommand, .maskShift])
        }
        // Share picker: choose "Screen N"/"Desktop N" or the display's own name, then press Share.
        let candidates = strings.tiles(for: display)
        var picked = false
        for _ in 0..<10 where !picked {
            sleeper.sleep(0.5)
            for w in app.windows {
                if let tile = w.find(depth: 10, { el in candidates.contains { el.label == $0 } }) {
                    tile.press()
                    if let btn = w.find(depth: 10, { $0.role == kAXButtonRole &&
                        strings.sharePickerButtons.contains($0.title) }) {
                        btn.press()
                    } else {
                        Keyboard.send(keyCode: 36 /* return */, flags: [])
                    }
                    picked = true
                    break
                }
            }
        }
        if !picked {
            if display.isMain {
                Keyboard.send(keyCode: 36, flags: [])  // picker defaults to the main screen
                return .keystroke
            }
            throw ZoomError.elementNotFound("share picker tile for \(candidates.joined(separator: " / "))")
        }
        return method
    }

    public func selectCamera(named: String) throws {
        let app = try app()
        guard let item = app.menuBar?.find(depth: 6, { $0.role == kAXMenuItemRole && $0.title == named }) else {
            throw ZoomError.elementNotFound("menu item for camera \(named)")
        }
        guard item.press() else { throw ZoomError.elementNotFound("pressable camera item \(named)") }
    }

    /// Snapshot of window titles and menu titles for `roomagent calibrate`.
    /// Throws when Accessibility is not granted (so calibration can't be misread as "no match").
    public func observe() throws -> ObservedUI {
        guard !MacZoomApp.apps().isEmpty else {
            return ObservedUI(zoomRunning: false, windowTitles: [], menuBarTitles: [], menuItemTitles: [])
        }
        let app = try app()
        var items: [String] = []
        func collect(_ el: AX, depth: Int) {
            if el.role == kAXMenuItemRole, !el.title.isEmpty { items.append(el.title) }
            guard depth > 0 else { return }
            el.children.forEach { collect($0, depth: depth - 1) }
        }
        let bar = app.menuBar
        bar.map { collect($0, depth: 4) }
        return ObservedUI(zoomRunning: true, windowTitles: app.windows.map(\.title),
                          menuBarTitles: bar?.children.map(\.title) ?? [], menuItemTitles: items)
    }

    public func dump(depth: Int) throws -> [String: Any] {
        let app = try app()
        return ["windows": app.windows.map { $0.dump(depth: depth) },
                "menuBar": app.menuBar?.dump(depth: min(depth, 4)) ?? [:]]
    }
}

enum Keyboard {
    static func send(keyCode: CGKeyCode, flags: CGEventFlags) {
        let src = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let ev = CGEvent(keyboardEventSource: src, virtualKey: keyCode, keyDown: down)
            ev?.flags = flags
            if let pid = MacZoomApp.apps().first?.processIdentifier {
                ev?.postToPid(pid)
            } else {
                ev?.post(tap: .cghidEventTap)
            }
        }
    }
}

public enum MacEnvironment {
    /// Throws if a UI-strings override file exists but is invalid (never silently ignored).
    public static func make() throws -> AgentEnvironment {
        let devices = MacDevices()
        let (strings, _) = try ZoomUIStrings.load()
        return AgentEnvironment(devices: devices, displays: devices, media: MacMedia(), session: MacSession(),
                                network: TCPReachability(), zoom: MacZoomApp(), ui: MacZoomUI(strings: strings),
                                audio: CoreAudioRouting(), sleeper: RunLoopSleeper())
    }
}
#endif
