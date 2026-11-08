// Transcribe an audio file and print the result — a test probe.
//
// Exists so the voice path is testable without a human speaking: `say`
// synthesizes a phrase, this transcribes it, and the test asserts the
// transcript routes correctly. "It worked when I tried it" is not a
// regression test.

import AVFoundation
import Foundation
import Speech

@available(macOS 26.0, *)
func transcribe(_ url: URL) async throws {
    let transcriber = SpeechTranscriber(
        locale: Locale(identifier: "en-US"),
        transcriptionOptions: [],
        reportingOptions: [],
        attributeOptions: []
    )
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let file = try AVAudioFile(forReading: url)

    let printer = Task {
        for try await result in transcriber.results {
            let text = String(result.text.characters)
            if !text.isEmpty { print("TRANSCRIPT: \(text)") }
        }
    }

    _ = try await analyzer.analyzeSequence(from: file)
    try await analyzer.finalizeAndFinishThroughEndOfInput()
    try? await Task.sleep(nanoseconds: 500_000_000)
    printer.cancel()
}

guard CommandLine.arguments.count > 1 else {
    FileHandle.standardError.write(Data("usage: transcribe-file <audio>\n".utf8))
    exit(2)
}

if #available(macOS 26.0, *) {
    let done = DispatchSemaphore(value: 0)
    Task {
        do { try await transcribe(URL(fileURLWithPath: CommandLine.arguments[1])) }
        catch { FileHandle.standardError.write(Data("error: \(error)\n".utf8)) }
        done.signal()
    }
    done.wait()
} else {
    FileHandle.standardError.write(Data("needs macOS 26\n".utf8))
    exit(1)
}
