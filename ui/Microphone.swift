// Microphone → transcripts — ARCHITECTURE.md §5.1, §7.
//
// macOS SpeechAnalyzer / SpeechTranscriber, which run entirely on-device:
// no network call anywhere in the transcription path. That is privacy
// claim (a) in the README, and it is the reason this is the primary STT
// rather than a downloaded Whisper model.
//
// Measured on this machine: the analyzer wants 16kHz mono Int16, the
// built-in mic gives 44.1kHz mono Float32, so every buffer is converted.
//
// Push-to-talk, not a wake word (ARCHITECTURE.md open question 4): no
// always-on mic, no accidental activation, and the user can see exactly
// when the agent is listening.

import AVFoundation
import Foundation
import Speech

@available(macOS 26.0, *)
final class Microphone {
    enum MicError: Error, CustomStringConvertible {
        case denied
        case noCompatibleFormat
        case localeUnavailable(String)

        var description: String {
            switch self {
            case .denied:
                return "microphone access denied"
            case .noCompatibleFormat:
                return "no audio format compatible with the transcriber"
            case .localeUnavailable(let id):
                return "speech model for \(id) is not installed"
            }
        }
    }

    private let engine = AVAudioEngine()
    private let locale: Locale
    private var transcriber: SpeechTranscriber?
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?

    /// Called with (text, isFinal) on the main queue.
    private let onTranscript: (String, Bool) -> Void

    init(locale: Locale = Locale(identifier: "en-US"),
         onTranscript: @escaping (String, Bool) -> Void) {
        self.locale = locale
        self.onTranscript = onTranscript
    }

    /// Ask for the mic. Separate from `start` so the prompt happens once,
    /// at launch, rather than the first time the user holds the key.
    static func requestAccess() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            return await AVCaptureDevice.requestAccess(for: .audio)
        default:
            return false
        }
    }

    func start() async throws {
        guard await Microphone.requestAccess() else { throw MicError.denied }

        let installed = await SpeechTranscriber.installedLocales.map(\.identifier)
        let wanted = locale.identifier.replacingOccurrences(of: "-", with: "_")
        guard installed.contains(where: { $0 == wanted }) else {
            throw MicError.localeUnavailable(locale.identifier)
        }

        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: []
        )
        let analyzer = SpeechAnalyzer(modules: [transcriber])

        guard
            let target = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber]
            )
        else { throw MicError.noCompatibleFormat }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        try await analyzer.start(inputSequence: stream)

        let input = engine.inputNode
        let hardware = input.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: hardware, to: target) else {
            throw MicError.noCompatibleFormat
        }

        input.installTap(onBus: 0, bufferSize: 4096, format: hardware) { buffer, _ in
            let ratio = target.sampleRate / hardware.sampleRate
            let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
            guard let converted = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity)
            else { return }

            var error: NSError?
            var supplied = false
            converter.convert(to: converted, error: &error) { _, status in
                if supplied {
                    status.pointee = .noDataNow
                    return nil
                }
                supplied = true
                status.pointee = .haveData
                return buffer
            }
            if error == nil, converted.frameLength > 0 {
                continuation.yield(AnalyzerInput(buffer: converted))
            }
        }

        engine.prepare()
        try engine.start()

        self.transcriber = transcriber
        self.analyzer = analyzer
        self.continuation = continuation

        resultsTask = Task { [onTranscript] in
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    guard !text.isEmpty else { continue }
                    let isFinal = result.isFinal
                    await MainActor.run { onTranscript(text, isFinal) }
                }
            } catch {
                // The stream ended. Nothing to recover: the caller sees
                // no further transcripts, which reads as "not listening".
            }
        }
    }

    func stop() async {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        continuation?.finish()
        continuation = nil
        if let analyzer {
            try? await analyzer.finalizeAndFinishThroughEndOfInput()
        }
        analyzer = nil
        transcriber = nil
        resultsTask?.cancel()
        resultsTask = nil
    }

    var isRunning: Bool { engine.isRunning }
}
