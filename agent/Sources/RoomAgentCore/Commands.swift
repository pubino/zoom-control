import Foundation

public let zoomEndpoints: [(String, UInt16)] = [("zoom.us", 443), ("api.zoom.us", 443)]

/// Command implementations. Pure functions of (environment, config) → Report.
public struct Commands {
    public let env: AgentEnvironment
    public var sampleSeconds: Double = 1.5
    public var pollInterval: Double = 1.0

    public init(env: AgentEnvironment) { self.env = env }

    // MARK: shared probes

    func resolveDisplay(_ cfg: RoomConfig) -> (DisplayInfo?, Check) {
        let displays = env.displays.displays()
        let names = displays.map(\.name)
        let m = matchName(cfg.display, in: names)
        var check = describe(m, kind: "display_present", wanted: cfg.display, available: names)
        guard case .found(let name) = m, let d = displays.first(where: { $0.name == name }) else { return (nil, check) }
        check.detail = "\(d.name) (#\(d.index), id \(d.id))"
        return (d, check)
    }

    func videoChecks(_ cfg: RoomConfig, missingIsFail: Bool = true) -> [Check] {
        let available = env.devices.videoDevices()
        let m = matchName(cfg.videoDevice, in: available)
        let present = describe(m, kind: "video_device", wanted: cfg.videoDevice, available: available)
        guard case .found(let name) = m else {
            return [present, Check("video_signal", .fail, "device unavailable")]
        }
        do {
            let s = try env.media.measureVideo(device: name, seconds: sampleSeconds)
            let fpsText = String(format: "%.1f fps, luma %.3f", s.fps, s.meanLuma)
            if s.fps < cfg.minFPS { return [present, Check("video_signal", .fail, "no frames: \(fpsText)")] }
            if s.meanLuma <= cfg.blackLuma { return [present, Check("video_signal", .warn, "black picture: \(fpsText)")] }
            return [present, Check("video_signal", .ok, fpsText)]
        } catch {
            return [present, Check("video_signal", .fail, "capture error: \(error)")]
        }
    }

    func audioChecks(_ cfg: RoomConfig, silentStatus: Status) -> [Check] {
        let available = env.devices.audioInputDevices()
        let m = matchName(cfg.audioDevice, in: available)
        let present = describe(m, kind: "audio_device", wanted: cfg.audioDevice, available: available)
        guard case .found(let name) = m else {
            return [present, Check("audio_signal", .fail, "device unavailable")]
        }
        do {
            let db = try env.media.measureAudio(device: name, seconds: sampleSeconds)
            let text = db.isFinite ? String(format: "%.1f dBFS", db) : "digital silence"
            return [present, Check("audio_signal", db < cfg.minDBFS ? silentStatus : .ok,
                                   db < cfg.minDBFS ? "below floor \(cfg.minDBFS) dBFS: \(text)" : text)]
        } catch {
            return [present, Check("audio_signal", .fail, "capture error: \(error)")]
        }
    }

    func displayLumaCheck(_ display: DisplayInfo?, _ cfg: RoomConfig, blackStatus: Status) -> Check {
        guard let d = display else { return Check("display_not_black", .fail, "display unavailable") }
        do {
            let luma = try env.media.displayLuma(displayID: d.id)
            let text = String(format: "luma %.3f", luma)
            return luma <= cfg.blackLuma ? Check("display_not_black", blackStatus, "black: \(text)")
                                         : Check("display_not_black", .ok, text)
        } catch {
            return Check("display_not_black", .fail, "screen capture error: \(error)")
        }
    }

    func sessionCheck() -> Check {
        if !env.session.isOnConsole() { return Check("screen_unlocked", .fail, "no GUI console session (auto-login?)") }
        if env.session.isScreenLocked() { return Check("screen_unlocked", .fail, "screen is locked") }
        return Check("screen_unlocked", .ok)
    }

    func poll(timeout: Double, _ condition: () -> Bool) -> Bool {
        var waited = 0.0
        while true {
            if condition() { return true }
            if waited >= timeout { return false }
            env.sleeper.sleep(pollInterval)
            waited += pollInterval
        }
    }

    // MARK: commands

    /// Hardware, session and network readiness. Quiet audio / dark picture only warn:
    /// a room before an event is legitimately silent.
    public func preflight(_ cfg: RoomConfig) -> Report {
        var checks = [sessionCheck()]
        let (display, dCheck) = resolveDisplay(cfg)
        checks.append(dCheck)
        checks += videoChecks(cfg)
        checks += audioChecks(cfg, silentStatus: .warn)
        checks.append(displayLumaCheck(display, cfg, blackStatus: .warn))
        for (host, port) in zoomEndpoints {
            let ok = env.network.canReach(host: host, port: port, timeout: 5)
            checks.append(Check("network_\(host)", ok ? .ok : .fail, ok ? "reachable" : "cannot reach \(host):\(port)"))
        }
        if env.zoom.isRunning() {
            if env.ui.isInMeeting() {
                checks.append(Check("zoom_idle", .fail, "Zoom is already in a meeting"))
            } else {
                let quit = env.zoom.quit(force: false, timeout: 10) || env.zoom.quit(force: true, timeout: 5)
                checks.append(Check("zoom_idle", quit ? .warn : .fail,
                                    quit ? "quit stale Zoom instance" : "could not quit stale Zoom instance"))
            }
        } else {
            checks.append(Check("zoom_idle", .ok))
        }
        return Report(command: "preflight", checks: checks)
    }

    public func launch(startURL: String, mode: LaunchMode, timeout: Double) -> Report {
        let url: URL
        do {
            url = try launchURL(from: startURL, mode: mode)
        } catch {
            return Report(command: "launch", checks: [Check("start_url", .fail, "\(error)")])
        }
        var checks = [Check("start_url", .ok, redact(url.absoluteString))]
        do {
            try env.zoom.open(url, mode: mode)
        } catch {
            return Report(command: "launch", checks: checks + [Check("zoom_running", .fail, "open failed: \(error)")])
        }
        let running = poll(timeout: min(30, timeout)) { env.zoom.isRunning() }
        checks.append(Check("zoom_running", running ? .ok : .fail, running ? "" : "zoom.us did not start"))
        guard running else { return Report(command: "launch", checks: checks) }
        let inMeeting = poll(timeout: timeout) { env.ui.isInMeeting() }
        checks.append(Check("in_meeting", inMeeting ? .ok : .fail,
                            inMeeting ? "" : "no meeting window after \(Int(timeout))s"))
        return Report(command: "launch", checks: checks)
    }

    public func share(_ cfg: RoomConfig, timeout: Double) -> Report {
        let (display, dCheck) = resolveDisplay(cfg)
        var checks = [dCheck]
        guard let display else { return Report(command: "share", checks: checks + [Check("sharing", .fail, "no display")]) }
        guard env.ui.isInMeeting() else {
            return Report(command: "share", checks: checks + [Check("sharing", .fail, "not in a meeting")])
        }
        if env.ui.isSharing() {
            return Report(command: "share", checks: checks + [Check("sharing", .ok, ShareMethod.alreadySharing.rawValue)])
        }
        do {
            let method = try env.ui.startShare(display: display)
            let ok = poll(timeout: timeout) { env.ui.isSharing() }
            checks.append(Check("sharing", ok ? .ok : .fail,
                                ok ? "via \(method.rawValue)" : "share not active after \(Int(timeout))s (via \(method.rawValue))"))
        } catch {
            checks.append(Check("sharing", .fail, "\(error)"))
        }
        return Report(command: "share", checks: checks)
    }

    public func selectAV(_ cfg: RoomConfig) -> Report {
        var checks: [Check] = []
        let audios = env.devices.audioInputDevices()
        let am = matchName(cfg.audioDevice, in: audios)
        if case .found(let name) = am {
            do {
                try env.audio.setDefaultInput(named: name)
                checks.append(Check("audio_route", .ok, "system default input = \(name)"))
            } catch {
                checks.append(Check("audio_route", .fail, "\(error)"))
            }
        } else {
            var c = describe(am, kind: "audio_route", wanted: cfg.audioDevice, available: audios)
            c.status = .fail
            checks.append(c)
        }
        let videos = env.devices.videoDevices()
        let vm = matchName(cfg.videoDevice, in: videos)
        if case .found(let name) = vm {
            do {
                try env.ui.selectCamera(named: name)
                checks.append(Check("video_route", .ok, "camera = \(name)"))
            } catch {
                checks.append(Check("video_route", .warn, "could not select camera in Zoom: \(error)"))
            }
        } else {
            checks.append(describe(vm, kind: "video_route", wanted: cfg.videoDevice, available: videos))
        }
        return Report(command: "select-av", checks: checks)
    }

    /// Runtime health. Names are the contract with zoomctl's recovery logic:
    /// zoom_running/in_meeting → relaunch, sharing → re-share, others → alert.
    public func health(_ cfg: RoomConfig) -> Report {
        var checks: [Check] = []
        let running = env.zoom.isRunning()
        checks.append(Check("zoom_running", running ? .ok : .fail, running ? "" : "zoom.us not running"))
        let inMeeting = running && env.ui.isInMeeting()
        checks.append(Check("in_meeting", inMeeting ? .ok : .fail, inMeeting ? "" : "no meeting window"))
        let sharing = inMeeting && env.ui.isSharing()
        checks.append(Check("sharing", sharing ? .ok : .fail, sharing ? "" : "screen share not active"))
        checks.append(sessionCheck())
        let (display, dCheck) = resolveDisplay(cfg)
        checks.append(dCheck)
        checks.append(displayLumaCheck(display, cfg, blackStatus: .fail))
        checks += videoChecks(cfg).filter { $0.name == "video_signal" }
        checks += audioChecks(cfg, silentStatus: .fail).filter { $0.name == "audio_signal" }
        return Report(command: "health", checks: checks)
    }

    public func quit(force: Bool) -> Report {
        guard env.zoom.isRunning() else { return Report(command: "quit", checks: [Check("zoom_stopped", .ok, "not running")]) }
        let ok = env.zoom.quit(force: force, timeout: force ? 5 : 15)
        return Report(command: "quit", checks: [Check("zoom_stopped", ok ? .ok : .fail, ok ? "" : "zoom.us still running")])
    }
}
