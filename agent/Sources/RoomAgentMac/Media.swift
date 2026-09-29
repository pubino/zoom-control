#if os(macOS)
import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import RoomAgentCore
import ScreenCaptureKit

enum MediaError: Error, CustomStringConvertible {
    case deviceNotFound(String)
    case notAuthorized(String)
    case cannotAddInput(String)
    case capture(String)

    var description: String {
        switch self {
        case .deviceNotFound(let n): return "capture device \(n) not found"
        case .notAuthorized(let what): return "\(what) access not granted (grant via PPPC profile — see docs/security.md)"
        case .cannotAddInput(let n): return "cannot open \(n) (in exclusive use?)"
        case .capture(let why): return why
        }
    }
}

/// Collects sample buffers for a fixed window and reduces them to a measurement.
private final class SampleCollector: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate,
    AVCaptureAudioDataOutputSampleBufferDelegate {
    private let lock = NSLock()
    private(set) var frames = 0
    private(set) var lastLuma = 0.0
    private var sumSquares = 0.0
    private var sampleCount = 0

    var rmsDBFS: Double {
        lock.lock(); defer { lock.unlock() }
        guard sampleCount > 0, sumSquares > 0 else { return -.infinity }
        return 20 * log10((sumSquares / Double(sampleCount)).squareRoot())
    }

    var videoFrames: (Int, Double) {
        lock.lock(); defer { lock.unlock() }
        return (frames, lastLuma)
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        if output is AVCaptureVideoDataOutput {
            let luma = CMSampleBufferGetImageBuffer(sampleBuffer).map(Self.meanLuma) ?? 0
            lock.lock(); frames += 1; lastLuma = luma; lock.unlock()
        } else {
            let (sq, n) = Self.floatPower(sampleBuffer)
            lock.lock(); sumSquares += sq; sampleCount += n; lock.unlock()
        }
    }

    /// Mean Rec.601 luma over a sparse grid of a 32BGRA pixel buffer.
    static func meanLuma(_ px: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(px, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(px, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(px) else { return 0 }
        let w = CVPixelBufferGetWidth(px), h = CVPixelBufferGetHeight(px), row = CVPixelBufferGetBytesPerRow(px)
        let ptr = base.assumingMemoryBound(to: UInt8.self)
        var total = 0.0, n = 0
        for y in stride(from: 0, to: h, by: max(1, h / 24)) {
            for x in stride(from: 0, to: w, by: max(1, w / 32)) {
                let p = ptr + y * row + x * 4
                total += (0.114 * Double(p[0]) + 0.587 * Double(p[1]) + 0.299 * Double(p[2])) / 255
                n += 1
            }
        }
        return n > 0 ? total / Double(n) : 0
    }

    /// Sum of squares of interleaved Float32 PCM (the format we request below).
    static func floatPower(_ sb: CMSampleBuffer) -> (Double, Int) {
        guard let block = CMSampleBufferGetDataBuffer(sb) else { return (0, 0) }
        var length = 0
        var data: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length,
                                          dataPointerOut: &data) == kCMBlockBufferNoErr, let data else { return (0, 0) }
        let count = length / MemoryLayout<Float32>.size
        return data.withMemoryRebound(to: Float32.self, capacity: count) { f in
            var s = 0.0
            for i in 0..<count { let v = Double(f[i]); s += v * v }
            return (s, count)
        }
    }
}

public struct MacMedia: MediaProbe {
    public init() {}

    private func authorize(_ type: AVMediaType, _ what: String) throws {
        switch AVCaptureDevice.authorizationStatus(for: type) {
        case .authorized: return
        case .notDetermined:
            let sem = DispatchSemaphore(value: 0)
            var granted = false
            AVCaptureDevice.requestAccess(for: type) { granted = $0; sem.signal() }
            _ = sem.wait(timeout: .now() + 10)
            if !granted { throw MediaError.notAuthorized(what) }
        default:
            throw MediaError.notAuthorized(what)
        }
    }

    private func run(session: AVCaptureSession, seconds: Double) {
        session.startRunning()
        RunLoop.current.run(until: Date().addingTimeInterval(seconds))
        session.stopRunning()
    }

    public func measureVideo(device: String, seconds: Double) throws -> VideoSample {
        try authorize(.video, "Camera")
        guard let dev = MacDevices.videoCaptureDevices().first(where: { $0.localizedName == device }) else {
            throw MediaError.deviceNotFound(device)
        }
        let session = AVCaptureSession()
        let input = try AVCaptureDeviceInput(device: dev)
        guard session.canAddInput(input) else { throw MediaError.cannotAddInput(device) }
        session.addInput(input)
        let out = AVCaptureVideoDataOutput()
        out.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        out.alwaysDiscardsLateVideoFrames = true
        let collector = SampleCollector()
        out.setSampleBufferDelegate(collector, queue: DispatchQueue(label: "roomagent.video"))
        guard session.canAddOutput(out) else { throw MediaError.capture("cannot add video output") }
        session.addOutput(out)
        run(session: session, seconds: seconds)
        let (frames, luma) = collector.videoFrames
        return VideoSample(fps: Double(frames) / seconds, meanLuma: luma)
    }

    public func measureAudio(device: String, seconds: Double) throws -> Double {
        try authorize(.audio, "Microphone")
        guard let dev = MacDevices.audioCaptureDevices().first(where: { $0.localizedName == device }) else {
            throw MediaError.deviceNotFound(device)
        }
        let session = AVCaptureSession()
        let input = try AVCaptureDeviceInput(device: dev)
        guard session.canAddInput(input) else { throw MediaError.cannotAddInput(device) }
        session.addInput(input)
        let out = AVCaptureAudioDataOutput()
        out.audioSettings = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let collector = SampleCollector()
        out.setSampleBufferDelegate(collector, queue: DispatchQueue(label: "roomagent.audio"))
        guard session.canAddOutput(out) else { throw MediaError.capture("cannot add audio output") }
        session.addOutput(out)
        run(session: session, seconds: seconds)
        return collector.rmsDBFS
    }

    public func displayLuma(displayID: UInt32) throws -> Double {
        guard CGPreflightScreenCaptureAccess() else { throw MediaError.notAuthorized("Screen Recording") }
        let sem = DispatchSemaphore(value: 0)
        var result: Result<Double, Error> = .failure(MediaError.capture("screen capture timed out"))
        Task.detached {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first(where: { $0.displayID == displayID }) else {
                    throw MediaError.capture("display \(displayID) not shareable")
                }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let cfg = SCStreamConfiguration()
                cfg.width = 64
                cfg.height = 36
                cfg.showsCursor = false
                let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                result = .success(Self.meanLuma(image))
            } catch {
                result = .failure(error)
            }
            sem.signal()
        }
        // Keep the main run loop alive while the async capture completes.
        let deadline = Date().addingTimeInterval(10)
        while sem.wait(timeout: .now()) == .timedOut && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        return try result.get()
    }

    static func meanLuma(_ image: CGImage) -> Double {
        let w = 32, h = 18
        var pixels = [UInt8](repeating: 0, count: w * h * 4)
        guard let ctx = CGContext(data: &pixels, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return 0 }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        var total = 0.0
        for i in stride(from: 0, to: pixels.count, by: 4) {
            total += (0.299 * Double(pixels[i]) + 0.587 * Double(pixels[i + 1]) + 0.114 * Double(pixels[i + 2])) / 255
        }
        return total / Double(w * h)
    }
}
#endif
