import AppKit

final class Controller: NSObject, NSApplicationDelegate {
    private var strip: NotchStrip!
    private var confirm: ConfirmPanel!
    private var settingsPanel: SettingsPanel!
    private var statusMenu: StatusMenuController?
    private var coreProcess: CoreProcess?
    private var pendingDecision: String?
    private var microphone: Any?
    private var listening = false
    private var monitor: Any?
    private var startTask: Task<Void, Never>?
    private var settings = Settings.load()
    private var currentPTTKey: NSEvent.ModifierFlags = .function
    private var screenChangeObserver: NSObjectProtocol?
    private var sleepObserver: NSObjectProtocol?
    private var wakeObserver: NSObjectProtocol?
    private var idleTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let screen = NotchGeometry.findNotchScreen() ?? NSScreen.main ?? NSScreen.screens[0]
        let geometry = NotchGeometry(screen: screen)

        if !geometry.hasNotch {
            FileHandle.standardError.write(
                Data("no notch on any display; strip renders at top-centre\n".utf8)
            )
        }

        currentPTTKey = settings.pushToTalkKey.modifierFlags

        strip = NotchStrip(geometry: geometry) { [weak self] in
            self?.toggleSettings()
        }

        confirm = ConfirmPanel(geometry: geometry) { [weak self] decision, approved in
            guard let self else { return }
            self.pendingDecision = nil
            self.emit(["t": "verdict", "decision_id": decision, "approve": approved])
            self.strip.render(.idle)
            self.scheduleAutoHideIfNeeded()
        }

        settingsPanel = SettingsPanel(settings: settings) { [weak self] newSettings in
            self?.applySettings(newSettings)
        }

        statusMenu = StatusMenuController()
        statusMenu?.updateMenu(stripVisible: settings.stripVisible, autoHideWhenIdle: settings.autoHideWhenIdle)
        statusMenu?.onToggleNotch = { [weak self] visible in
            guard let self else { return }
            var updated = self.settings
            updated.stripVisible = visible
            self.applySettings(updated)
            self.settingsPanel.updateSettings(updated)
        }
        statusMenu?.onToggleAutoHide = { [weak self] autoHide in
            guard let self else { return }
            var updated = self.settings
            updated.autoHideWhenIdle = autoHide
            self.applySettings(updated)
            self.settingsPanel.updateSettings(updated)
        }
        statusMenu?.onOpenSettings = { [weak self] in
            self?.openSettings()
        }
        statusMenu?.onQuit = {
            NSApp.terminate(nil)
        }

        updateStripVisibility()

        setupScreenChangeDetection()
        setupSleepWakeHandling()
        startCoreIfHosted()
        setUpPushToTalk()
    }

    // MARK: - settings & visibility

    private func applySettings(_ newSettings: Settings) {
        settings = newSettings
        currentPTTKey = newSettings.pushToTalkKey.modifierFlags
        settings.save()
        emit(["t": "settings_changed", "settings": settingsDict()])
        updateStripVisibility()
        statusMenu?.updateMenu(stripVisible: newSettings.stripVisible, autoHideWhenIdle: newSettings.autoHideWhenIdle)
    }

    private func updateStripVisibility() {
        if !settings.stripVisible {
            strip.hide()
        } else if settings.autoHideWhenIdle && !listening {
            strip.hide()
        } else {
            strip.show()
        }
    }

    private func scheduleAutoHideIfNeeded() {
        guard settings.autoHideWhenIdle, settings.stripVisible else { return }
        idleTimer?.invalidate()
        idleTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: false) { [weak self] _ in
            guard let self, !self.listening, self.pendingDecision == nil else { return }
            self.strip.hide()
        }
    }

    private func settingsDict() -> [String: Any] {
        [
            "mic_enabled": settings.micEnabled,
            "push_to_talk_key": settings.pushToTalkKey.rawValue,
            "strip_visible": settings.stripVisible,
            "auto_hide_when_idle": settings.autoHideWhenIdle,
            "confirm_timeout": settings.confirmTimeout,
            "reduce_motion": settings.reduceMotion,
        ]
    }

    private func toggleSettings() {
        if settingsPanel.isVisible() {
            settingsPanel.dismiss()
        } else {
            openSettings()
        }
    }

    private func openSettings() {
        let screen = NotchGeometry.findNotchScreen() ?? NSScreen.main ?? NSScreen.screens[0]
        let geometry = NotchGeometry(screen: screen)
        settingsPanel.present(geometry: geometry)
    }

    // MARK: - screen change detection

    private func setupScreenChangeDetection() {
        screenChangeObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.handleScreenChange()
        }
    }

    private func handleScreenChange() {
        let screen = NotchGeometry.findNotchScreen() ?? NSScreen.main ?? NSScreen.screens[0]
        let geometry = NotchGeometry(screen: screen)
        strip.reposition(geometry: geometry)
        if settingsPanel.isVisible() {
            settingsPanel.dismiss()
            settingsPanel.present(geometry: geometry)
        }
    }

    // MARK: - sleep/wake handling

    private func setupSleepWakeHandling() {
        let workspace = NSWorkspace.shared.notificationCenter
        sleepObserver = workspace.addObserver(
            forName: NSWorkspace.willSleepNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.handleSleep()
        }
        wakeObserver = workspace.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.handleWake()
        }
    }

    private func handleSleep() {
        endListeningIfNeeded()
    }

    private func handleWake() {
        updateStripVisibility()
    }

    // MARK: - push to talk

    private func setUpPushToTalk() {
        guard #available(macOS 26.0, *) else {
            FileHandle.standardError.write(Data("mic needs macOS 26\n".utf8))
            return
        }
        guard settings.micEnabled else {
            FileHandle.standardError.write(Data("mic disabled in settings\n".utf8))
            return
        }
        let mic = Microphone { [weak self] text, isFinal in
            self?.deliver(text: text, isFinal: isFinal)
        }
        microphone = mic

        monitor = NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged]) {
            [weak self] event in
            guard let self else { return }
            let held = event.modifierFlags.contains(self.currentPTTKey)
            if held, !self.listening {
                self.beginListening(mic)
            } else if !held, self.listening {
                self.endListening(mic)
            }
        }

        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self, self.listening else { return }
            if !NSEvent.modifierFlags.contains(self.currentPTTKey) {
                self.endListening(mic)
            }
        }
        if monitor == nil {
            FileHandle.standardError.write(
                Data("push-to-talk needs Accessibility permission\n".utf8)
            )
        }
    }

    @available(macOS 26.0, *)
    private func beginListening(_ mic: Microphone) {
        listening = true
        idleTimer?.invalidate()
        if settings.stripVisible {
            strip.show()
        }
        strip.render(.listening)
        statusMenu?.setStatus("listening")
        startTask = Task {
            do { try await mic.start() } catch {
                FileHandle.standardError.write(Data("mic: \(error)\n".utf8))
                await MainActor.run {
                    self.listening = false
                    self.strip.render(.failed)
                    self.statusMenu?.setStatus("failed")
                    self.scheduleAutoHideIfNeeded()
                }
            }
        }
    }

    @available(macOS 26.0, *)
    private func endListening(_ mic: Microphone) {
        listening = false
        strip.render(.idle)
        statusMenu?.setStatus("idle")
        scheduleAutoHideIfNeeded()
        let pending = startTask
        startTask = nil
        Task {
            await pending?.value
            await mic.stop()
        }
    }

    private func endListeningIfNeeded() {
        if listening, #available(macOS 26.0, *), let mic = microphone as? Microphone {
            endListening(mic)
        }
    }

    private func deliver(text: String, isFinal: Bool) {
        guard pendingDecision == nil else { return }
        guard isFinal else { return }
        emit(["t": "final", "text": text])
    }

    // MARK: - Hosted mode (Swift spawns Python core)

    private func startCoreIfHosted() {
        let args = CommandLine.arguments
        var pythonPath: String?
        var coreDir: String?

        if let hostIndex = args.firstIndex(of: "--host"), args.count > hostIndex + 2 {
            pythonPath = args[hostIndex + 1]
            coreDir = args[hostIndex + 2]
        } else if let infoDict = Bundle.main.infoDictionary,
                  let bundledPython = (infoDict["FridayCorePython"] ?? infoDict["MBCorePython"]) as? String,
                  let bundledCoreDir = (infoDict["FridayCoreDir"] ?? infoDict["MBCoreDir"]) as? String
        {
            pythonPath = bundledPython
            coreDir = Bundle.main.resourceURL?
                .appendingPathComponent(bundledCoreDir).path ?? bundledCoreDir
        }

        guard let pythonPath, let coreDir else {
            // Dev mode: Python already spawned us and owns stdin/stdout.
            readLoop()
            return
        }

        coreProcess = CoreProcess(
            pythonPath: pythonPath,
            coreDir: coreDir,
            onLine: { [weak self] line in self?.handleLine(line) },
            onExit: { [weak self] _ in self?.onCoreExited() }
        )

        if coreProcess == nil {
            FileHandle.standardError.write(Data("failed to start hosted core\n".utf8))
            strip.render(.failed)
            statusMenu?.setStatus("failed")
        }
    }

    private func onCoreExited() {
        pendingDecision = nil
        confirm.dismiss()
        statusMenu?.setStatus("stopped")
        strip.render(.failed)
        scheduleAutoHideIfNeeded()
    }

    // MARK: - read loop (Dev mode: Python spawned Swift)

    private func readLoop() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let line = readLine(strippingNewline: true) {
                self?.handleLine(line)
            }
            DispatchQueue.main.async {
                self?.pendingDecision = nil
                self?.confirm.dismiss()
                self?.strip.render(.failed)
                exit(0)
            }
        }
    }

    private func handleLine(_ line: String) {
        guard
            let data = line.data(using: .utf8),
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let kind = payload["t"] as? String
        else { return }

        if Thread.isMainThread {
            handle(kind: kind, payload: payload)
        } else {
            DispatchQueue.main.async { [weak self] in self?.handle(kind: kind, payload: payload) }
        }
    }

    private func handle(kind: String, payload: [String: Any]) {
        switch kind {
        case "state":
            if let raw = payload["state"] as? String, let state = AgentState(rawValue: raw) {
                idleTimer?.invalidate()
                if settings.stripVisible && (!settings.autoHideWhenIdle || state != .idle) {
                    strip.show()
                }
                strip.render(state)
                statusMenu?.setStatus(raw)
                if state == .idle {
                    scheduleAutoHideIfNeeded()
                }
            }

        case "confirm":
            guard
                let decision = payload["decision_id"] as? String,
                let description = payload["description"] as? String
            else { return }
            pendingDecision = decision
            settingsPanel.dismiss()
            idleTimer?.invalidate()
            if settings.stripVisible {
                strip.show()
            }
            confirm.present(
                decisionID: decision,
                description: description,
                verbatim: payload["verbatim"] as? Bool ?? true
            )

        case "dismiss":
            pendingDecision = nil
            confirm.dismiss()
            scheduleAutoHideIfNeeded()

        case "settings":
            if let settingsData = payload["settings"] as? [String: Any] {
                var newSettings = Settings()
                if let v = settingsData["mic_enabled"] as? Bool { newSettings.micEnabled = v }
                if let v = settingsData["push_to_talk_key"] as? String { newSettings.pushToTalkKey = PushToTalkKey(rawValue: v) ?? .fn }
                if let v = settingsData["strip_visible"] as? Bool { newSettings.stripVisible = v }
                if let v = settingsData["auto_hide_when_idle"] as? Bool { newSettings.autoHideWhenIdle = v }
                if let v = settingsData["confirm_timeout"] as? Double { newSettings.confirmTimeout = v }
                if let v = settingsData["reduce_motion"] as? Bool { newSettings.reduceMotion = v }
                applySettings(newSettings)
                settingsPanel.updateSettings(newSettings)
            }

        default:
            break
        }
    }

    private func emit(_ payload: [String: Any]) {
        guard
            let data = try? JSONSerialization.data(withJSONObject: payload),
            let text = String(data: data, encoding: .utf8)
        else { return }

        if let coreProcess {
            try? coreProcess.write(text)
        } else {
            FileHandle.standardOutput.write(Data((text + "\n").utf8))
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let coreProcess else { return .terminateNow }

        endListeningIfNeeded()
        confirm.dismiss()

        DispatchQueue.global(qos: .userInitiated).async {
            coreProcess.terminate()
            DispatchQueue.main.async {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = Controller()
app.delegate = controller
app.run()
