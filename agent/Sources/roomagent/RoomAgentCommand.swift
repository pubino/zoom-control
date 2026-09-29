#if os(macOS)
import ArgumentParser
import Foundation
import RoomAgentCore
import RoomAgentMac

struct RoomFlags: ParsableArguments {
    @Option(help: "Capture device name (AVFoundation)") var videoDevice: String
    @Option(help: "Audio input device name (CoreAudio)") var audioDevice: String
    @Option(help: "Display name to share (NSScreen.localizedName)") var display: String
    @Option(help: "Minimum capture frame rate") var minFps: Double = 1
    @Option(help: "Audio RMS floor in dBFS") var minDbfs: Double = -70
    @Option(help: "Mean luma at or below which a picture is black") var blackLuma: Double = 0.02

    var config: RoomConfig {
        RoomConfig(videoDevice: videoDevice, audioDevice: audioDevice, display: display, minFPS: minFps,
                   minDBFS: minDbfs, blackLuma: blackLuma)
    }
}

struct OutputFlags: ParsableArguments {
    @Flag(help: "Pretty-print JSON") var pretty = false
}

/// Every subcommand prints exactly one JSON Report and exits 0 (ok/warn) or 2 (fail).
protocol ReportingCommand: ParsableCommand {
    var output: OutputFlags { get }
    func report(_ commands: Commands) -> Report
}

extension ReportingCommand {
    func run() throws {
        let env: AgentEnvironment
        do { env = try MacEnvironment.make() } catch {
            print(Report(command: Self._commandName, checks: [], error: "\(error)").json(pretty: output.pretty))
            throw ExitCode(2)
        }
        let r = report(Commands(env: env))
        print(r.json(pretty: output.pretty))
        if r.exitCode != 0 { throw ExitCode(r.exitCode) }
    }
}

struct RoomAgentCommand: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "roomagent",
        abstract: "macOS room node agent for zoom-control. Emits one JSON report per command.",
        version: roomAgentVersion,
        subcommands: [Preflight.self, Launch.self, Share.self, SelectAV.self, Health.self, Quit.self,
                      Devices.self, Calibrate.self, UIStrings.self, AXDump.self]
    )
}

struct Preflight: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "Check hardware, session and network readiness.")
    @OptionGroup var room: RoomFlags
    @OptionGroup var output: OutputFlags
    func report(_ c: Commands) -> Report { c.preflight(room.config) }
}

struct Launch: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "Start the webinar as host from a start_url.")
    @Flag(help: "Read the start_url from stdin (keeps the ZAK token out of argv)") var startUrlStdin = false
    @Option(help: "Launch mode: zoommtg | https") var mode: String = LaunchMode.zoommtg.rawValue
    @Option(help: "Seconds to wait for the meeting window") var timeout: Double = 90
    @OptionGroup var output: OutputFlags

    func validate() throws {
        guard startUrlStdin else { throw ValidationError("--start-url-stdin is required") }
        guard LaunchMode(rawValue: mode) != nil else { throw ValidationError("--mode must be zoommtg or https") }
    }

    func report(_ c: Commands) -> Report {
        guard let url = readLine(strippingNewline: true), !url.isEmpty else {
            return Report(command: "launch", checks: [Check("start_url", .fail, "no start_url on stdin")])
        }
        return c.launch(startURL: url, mode: LaunchMode(rawValue: mode)!, timeout: timeout)
    }
}

struct Share: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "Share the configured display and verify it.")
    @OptionGroup var room: RoomFlags
    @Option(help: "Seconds to wait for sharing to become active") var timeout: Double = 20
    @OptionGroup var output: OutputFlags
    func report(_ c: Commands) -> Report { c.share(room.config, timeout: timeout) }
}

struct SelectAV: ReportingCommand {
    static let configuration = CommandConfiguration(commandName: "select-av",
                                                    abstract: "Route room mic (system default) and camera (Zoom).")
    @OptionGroup var room: RoomFlags
    @OptionGroup var output: OutputFlags
    func report(_ c: Commands) -> Report { c.selectAV(room.config) }
}

struct Health: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "One health sample during a live event.")
    @OptionGroup var room: RoomFlags
    @OptionGroup var output: OutputFlags
    func report(_ c: Commands) -> Report { c.health(room.config) }
}

struct Quit: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "Quit Zoom (gracefully unless --force).")
    @Flag var force = false
    @OptionGroup var output: OutputFlags
    func report(_ c: Commands) -> Report { c.quit(force: force) }
}

struct Devices: ReportingCommand {
    static let configuration = CommandConfiguration(abstract: "List capture devices and displays (for room YAML).")
    @OptionGroup var output: OutputFlags

    func report(_ c: Commands) -> Report {
        let env = c.env
        var checks = env.devices.videoDevices().map { Check("video", .ok, $0) }
        checks += env.devices.audioInputDevices().map { Check("audio", .ok, $0) }
        checks += env.displays.displays().map { Check("display", .ok, "\($0.name) (#\($0.index)\($0.isMain ? ", main" : ""))") }
        return Report(command: "devices", checks: checks)
    }
}

struct Calibrate: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Check Zoom UI strings against a live test meeting (run once not sharing, once with --sharing).")
    @Flag(help: "You are currently sharing a screen in the test meeting") var sharing = false
    @OptionGroup var output: OutputFlags

    func run() throws {
        let r: Report
        do {
            let (strings, source) = try ZoomUIStrings.load()
            let observed = try MacZoomUI(strings: strings).observe()
            r = calibrationReport(observed, strings: strings, source: source, expectSharing: sharing)
        } catch {
            r = Report(command: "calibrate", checks: [], error: "\(error)")
        }
        print(r.json(pretty: output.pretty))
        if r.exitCode != 0 { throw ExitCode(r.exitCode) }
    }
}

struct UIStrings: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "ui-strings",
        abstract: "Print the effective Zoom UI strings as JSON (copy to ui-strings.json to override).")

    func run() throws {
        do {
            let (strings, source) = try ZoomUIStrings.load()
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            FileHandle.standardError.write(Data("# source: \(source)\n".utf8))
            print(String(decoding: try enc.encode(strings), as: UTF8.self))
        } catch {
            print(Report(command: "ui-strings", checks: [], error: "\(error)").json())
            throw ExitCode(2)
        }
    }
}

struct AXDump: ParsableCommand {
    static let configuration = CommandConfiguration(commandName: "ax-dump",
                                                    abstract: "Dump Zoom's Accessibility tree to calibrate UI strings.")
    @Option var depth: Int = 6

    func run() throws {
        do {
            let tree = try MacZoomUI(strings: try ZoomUIStrings.load().0).dump(depth: depth)
            let data = try JSONSerialization.data(withJSONObject: tree, options: [.prettyPrinted, .sortedKeys])
            print(String(decoding: data, as: UTF8.self))
        } catch {
            print(Report(command: "ax-dump", checks: [], error: "\(error)").json())
            throw ExitCode(2)
        }
    }
}
#endif
