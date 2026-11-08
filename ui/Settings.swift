import AppKit
import Foundation

struct Settings: Codable {
    var micEnabled: Bool = true
    var pushToTalkKey: PushToTalkKey = .fn
    var stripVisible: Bool = true
    var autoHideWhenIdle: Bool = false
    var confirmTimeout: Double = 120.0
    var reduceMotion: Bool = false

    enum CodingKeys: String, CodingKey {
        case micEnabled = "mic_enabled"
        case pushToTalkKey = "push_to_talk_key"
        case stripVisible = "strip_visible"
        case autoHideWhenIdle = "auto_hide_when_idle"
        case confirmTimeout = "confirm_timeout"
        case reduceMotion = "reduce_motion"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        micEnabled = try c.decodeIfPresent(Bool.self, forKey: .micEnabled) ?? true
        pushToTalkKey = try c.decodeIfPresent(PushToTalkKey.self, forKey: .pushToTalkKey) ?? .fn
        stripVisible = try c.decodeIfPresent(Bool.self, forKey: .stripVisible) ?? true
        autoHideWhenIdle = try c.decodeIfPresent(Bool.self, forKey: .autoHideWhenIdle) ?? false
        confirmTimeout = try c.decodeIfPresent(Double.self, forKey: .confirmTimeout) ?? 120.0
        reduceMotion = try c.decodeIfPresent(Bool.self, forKey: .reduceMotion) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(micEnabled, forKey: .micEnabled)
        try c.encode(pushToTalkKey, forKey: .pushToTalkKey)
        try c.encode(stripVisible, forKey: .stripVisible)
        try c.encode(autoHideWhenIdle, forKey: .autoHideWhenIdle)
        try c.encode(confirmTimeout, forKey: .confirmTimeout)
        try c.encode(reduceMotion, forKey: .reduceMotion)
    }

    static var settingsURL: URL {
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/friday-local", isDirectory: true)
        return dir.appendingPathComponent("settings.json")
    }

    static func load() -> Settings {
        guard let data = try? Data(contentsOf: settingsURL),
              let settings = try? JSONDecoder().decode(Settings.self, from: data)
        else { return Settings() }
        return settings
    }

    func save() {
        let url = Settings.settingsURL
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        if let data = try? JSONEncoder().encode(self) {
            try? data.write(to: url)
        }
    }
}

enum PushToTalkKey: String, Codable, CaseIterable {
    case fn = "fn"
    case cmd = "cmd"
    case opt = "opt"
    case ctrl = "ctrl"
    case shift = "shift"

    var modifierFlags: NSEvent.ModifierFlags {
        switch self {
        case .fn: return .function
        case .cmd: return .command
        case .opt: return .option
        case .ctrl: return .control
        case .shift: return .shift
        }
    }

    var displayName: String {
        switch self {
        case .fn: return "Fn key"
        case .cmd: return "Cmd key"
        case .opt: return "Option key"
        case .ctrl: return "Control key"
        case .shift: return "Shift key"
        }
    }
}

final class ClickableView: NSView {
    var onClick: (() -> Void)?

    override func mouseDown(with event: NSEvent) {
        onClick?()
    }
}

final class KeyablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    var onCancel: (() -> Void)?

    override func cancelOperation(_ sender: Any?) {
        onCancel?()
    }
}

final class SettingsPanel {
    private var panel: NSPanel?
    private let onSettingsChanged: (Settings) -> Void
    private var settings: Settings
    private var micCheckbox: NSButton?
    private var keyPopup: NSPopUpButton?
    private var stripCheckbox: NSButton?
    private var autoHideCheckbox: NSButton?
    private var timeoutSlider: NSSlider?
    private var timeoutLabel: NSTextField?
    private var reduceMotionCheckbox: NSButton?

    init(settings: Settings, onSettingsChanged: @escaping (Settings) -> Void) {
        self.settings = settings
        self.onSettingsChanged = onSettingsChanged
    }

    func present(geometry: NotchGeometry) {
        dismiss()

        let width: CGFloat = 320
        let inset: CGFloat = 20

        let title = NSTextField(labelWithString: "Friday Settings")
        title.font = .systemFont(ofSize: 15, weight: .bold)

        let doneButton = NSButton(title: "Done", target: self, action: #selector(doneTapped))
        doneButton.bezelStyle = .rounded
        doneButton.keyEquivalent = "\r"

        let header = NSStackView(views: [title, NSView(), doneButton])
        header.orientation = .horizontal
        header.distribution = .fill

        let micCheckbox = NSButton(checkboxWithTitle: "Microphone enabled", target: self, action: #selector(micToggled))
        micCheckbox.state = settings.micEnabled ? .on : .off
        self.micCheckbox = micCheckbox

        let keyLabel = NSTextField(labelWithString: "Push-to-talk key:")
        keyLabel.font = .systemFont(ofSize: 12)
        let keyPopup = NSPopUpButton()
        keyPopup.target = self
        keyPopup.action = #selector(keyChanged)
        for key in PushToTalkKey.allCases {
            keyPopup.addItem(withTitle: key.displayName)
            keyPopup.lastItem?.representedObject = key.rawValue
        }
        if let index = PushToTalkKey.allCases.firstIndex(where: { $0 == settings.pushToTalkKey }) {
            keyPopup.selectItem(at: index)
        }
        self.keyPopup = keyPopup

        let keyRow = NSStackView(views: [keyLabel, keyPopup])
        keyRow.orientation = .horizontal
        keyRow.spacing = 8

        let stripCheckbox = NSButton(checkboxWithTitle: "Show notch overlay", target: self, action: #selector(stripToggled))
        stripCheckbox.state = settings.stripVisible ? .on : .off
        self.stripCheckbox = stripCheckbox

        let autoHideCheckbox = NSButton(checkboxWithTitle: "Auto-hide notch when idle", target: self, action: #selector(autoHideToggled))
        autoHideCheckbox.state = settings.autoHideWhenIdle ? .on : .off
        self.autoHideCheckbox = autoHideCheckbox

        let timeoutLabel = NSTextField(labelWithString: "Confirm timeout: \(Int(settings.confirmTimeout))s")
        timeoutLabel.font = .systemFont(ofSize: 12)
        self.timeoutLabel = timeoutLabel
        let timeoutSlider = NSSlider(value: settings.confirmTimeout, minValue: 30, maxValue: 300, target: self, action: #selector(timeoutChanged))
        timeoutSlider.numberOfTickMarks = 10
        self.timeoutSlider = timeoutSlider

        let reduceMotionCheckbox = NSButton(checkboxWithTitle: "Reduce motion", target: self, action: #selector(reduceMotionToggled))
        reduceMotionCheckbox.state = settings.reduceMotion ? .on : .off
        self.reduceMotionCheckbox = reduceMotionCheckbox

        let stack = NSStackView(views: [
            header,
            micCheckbox,
            keyRow,
            stripCheckbox,
            autoHideCheckbox,
            timeoutLabel,
            timeoutSlider,
            reduceMotionCheckbox
        ])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: inset, left: inset, bottom: inset, right: inset)

        let fitted = stack.fittingSize
        let height = max(fitted.height, 220)

        let anchorX = geometry.notch?.midX ?? geometry.screen.frame.midX
        let frame = NSRect(
            x: anchorX - width / 2,
            y: geometry.screen.frame.maxY - geometry.screen.safeAreaInsets.top - height - 8,
            width: width,
            height: height
        )

        let created = KeyablePanel(
            contentRect: frame,
            styleMask: [.borderless, .titled, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        created.titleVisibility = .hidden
        created.titlebarAppearsTransparent = true
        created.isFloatingPanel = true
        created.level = .statusBar
        created.isMovable = false
        created.hidesOnDeactivate = false
        created.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        created.contentView = stack

        created.onCancel = { [weak self] in
            self?.dismiss()
        }

        created.makeKeyAndOrderFront(nil)
        panel = created
    }

    func dismiss() {
        panel?.orderOut(nil)
        panel = nil
    }

    func isVisible() -> Bool { panel != nil }

    func updateSettings(_ newSettings: Settings) {
        settings = newSettings
        micCheckbox?.state = newSettings.micEnabled ? .on : .off
        stripCheckbox?.state = newSettings.stripVisible ? .on : .off
        autoHideCheckbox?.state = newSettings.autoHideWhenIdle ? .on : .off
        reduceMotionCheckbox?.state = newSettings.reduceMotion ? .on : .off
        if let index = PushToTalkKey.allCases.firstIndex(where: { $0 == newSettings.pushToTalkKey }) {
            keyPopup?.selectItem(at: index)
        }
        timeoutSlider?.doubleValue = newSettings.confirmTimeout
        timeoutLabel?.stringValue = "Confirm timeout: \(Int(newSettings.confirmTimeout))s"
    }

    @objc private func doneTapped() {
        dismiss()
    }

    @objc private func micToggled(_ sender: NSButton) {
        settings.micEnabled = sender.state == .on
        settings.save()
        onSettingsChanged(settings)
    }

    @objc private func keyChanged(_ sender: NSPopUpButton) {
        guard let raw = sender.selectedItem?.representedObject as? String,
              let key = PushToTalkKey(rawValue: raw) else { return }
        settings.pushToTalkKey = key
        settings.save()
        onSettingsChanged(settings)
    }

    @objc private func stripToggled(_ sender: NSButton) {
        settings.stripVisible = sender.state == .on
        settings.save()
        onSettingsChanged(settings)
    }

    @objc private func autoHideToggled(_ sender: NSButton) {
        settings.autoHideWhenIdle = sender.state == .on
        settings.save()
        onSettingsChanged(settings)
    }

    @objc private func timeoutChanged(_ sender: NSSlider) {
        settings.confirmTimeout = sender.doubleValue
        timeoutLabel?.stringValue = "Confirm timeout: \(Int(settings.confirmTimeout))s"
        settings.save()
        onSettingsChanged(settings)
    }

    @objc private func reduceMotionToggled(_ sender: NSButton) {
        settings.reduceMotion = sender.state == .on
        settings.save()
        onSettingsChanged(settings)
    }
}
