// CoreProcess — Host the Python core as a child process
//
// When Swift is the top-level process (bundled .app mode), use this to spawn
// the Python core and manage its lifecycle. For dev mode (Python spawns Swift),
// this is not used.

import Foundation

@available(macOS 10.13, *)
class CoreProcess {
    private let process: Process
    private let stdinPipe: Pipe
    private let stdoutPipe: Pipe

    private(set) var isRunning: Bool = false
    private var readTask: Task<Void, Never>?
    private var watchdogTask: Task<Void, Never>?

    private let onLine: (String) -> Void
    private let onExit: (Int32) -> Void

    /// Spawn the Python core as a child process.
    init?(
        pythonPath: String,
        coreDir: String,
        onLine: @escaping (String) -> Void,
        onExit: @escaping (Int32) -> Void
    ) {
        self.onLine = onLine
        self.onExit = onExit

        process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = [
            "-m", "friday.cli",
            "--hosted"
        ]

        // Set PYTHONPATH to find the core package
        var env = ProcessInfo.processInfo.environment
        let pythonPathEnv = coreDir + ":" + (env["PYTHONPATH"] ?? "")
        env["PYTHONPATH"] = pythonPathEnv
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUTF8"] = "1"
        process.environment = env

        // Set working directory
        process.currentDirectoryURL = URL(fileURLWithPath: coreDir)

        stdinPipe = Pipe()
        stdoutPipe = Pipe()

        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = CoreProcess.coreLogHandle()

        process.terminationHandler = { [weak self] proc in
            guard let self else { return }
            self.isRunning = false
            self.onExit(proc.terminationStatus)
        }

        do {
            try process.run()
            isRunning = true
            startReading()
            startWatchdog()
        } catch {
            FileHandle.standardError.write(
                Data("failed to spawn core: \(error)\n".utf8)
            )
            return nil
        }
    }

    /// Send a line to the core's stdin.
    func write(_ line: String) throws {
        guard isRunning else {
            throw CoreProcessError.notRunning
        }
        guard let data = (line + "\n").data(using: .utf8) else {
            throw CoreProcessError.encodingFailed
        }
        do {
            try stdinPipe.fileHandleForWriting.write(contentsOf: data)
        } catch {
            throw CoreProcessError.writeFailed(error)
        }
    }

    /// Close the core's stdin (signals it to start shutdown).
    func closeStdin() {
        try? stdinPipe.fileHandleForWriting.close()
    }

    /// Terminate the core: close stdin first, then escalate to SIGTERM and SIGKILL of the process group.
    func terminate(gracePeriod: TimeInterval = 2.0, killGrace: TimeInterval = 3.0) {
        guard isRunning else { return }

        closeStdin()
        if waitForExit(timeout: gracePeriod) { return }

        let pid = process.processIdentifier
        if pid > 0 {
            kill(-pid, SIGTERM)
        }
        if waitForExit(timeout: killGrace) { return }

        if pid > 0 {
            kill(-pid, SIGKILL)
        }
        _ = waitForExit(timeout: 1.0)
    }

    func waitForExit(timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while isRunning && Date() < deadline {
            usleep(50_000)  // 50ms poll
        }
        return !isRunning
    }

    private func startReading() {
        readTask = Task {
            let handle = stdoutPipe.fileHandleForReading
            var buffer = Data()
            while !Task.isCancelled {
                let data = handle.availableData
                guard !data.isEmpty else {
                    return
                }
                buffer.append(data)

                while let newlineIndex = buffer.firstIndex(of: 0x0A) {
                    let lineData = buffer[..<newlineIndex]
                    buffer.removeSubrange(...newlineIndex)
                    guard let line = String(data: lineData, encoding: .utf8),
                          !line.isEmpty
                    else { continue }
                    let onLine = self.onLine
                    await MainActor.run { onLine(line) }
                }
            }
        }
    }

    private func startWatchdog() {
        watchdogTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)  // 0.5s
                if !process.isRunning {
                    return
                }
            }
        }
    }

    deinit {
        readTask?.cancel()
        watchdogTask?.cancel()
        if isRunning {
            terminate()
        }
    }
}

@available(macOS 10.13, *)
extension CoreProcess {
    static func coreLogHandle() -> FileHandle {
        let fm = FileManager.default
        guard
            let logsDir = fm.urls(for: .libraryDirectory, in: .userDomainMask).first?
                .appendingPathComponent("Logs/friday", isDirectory: true)
        else {
            return FileHandle.nullDevice
        }

        do {
            try fm.createDirectory(at: logsDir, withIntermediateDirectories: true)
        } catch {
            return FileHandle.nullDevice
        }

        let logFile = logsDir.appendingPathComponent("core.log")
        if !fm.fileExists(atPath: logFile.path) {
            fm.createFile(atPath: logFile.path, contents: nil)
        }

        guard let handle = try? FileHandle(forWritingTo: logFile) else {
            return FileHandle.nullDevice
        }
        handle.seekToEndOfFile()
        return handle
    }
}

enum CoreProcessError: Error {
    case notRunning
    case encodingFailed
    case writeFailed(Error)
}
