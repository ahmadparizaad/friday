// StatusMenu — Status bar item for Friday
//
// A menu bar icon (NSStatusItem) showing current state and providing quick access
// to toggling the notch overlay, Settings, and Quit.

import Cocoa

class StatusMenuController: NSObject {
    private let statusItem: NSStatusItem
    private let menu: NSMenu
    private let statusMenuItem: NSMenuItem
    private var toggleNotchMenuItem: NSMenuItem?
    private var toggleAutoHideMenuItem: NSMenuItem?

    var onOpenSettings: (() -> Void)?
    var onToggleNotch: ((Bool) -> Void)?
    var onToggleAutoHide: ((Bool) -> Void)?
    var onQuit: (() -> Void)?

    override init() {
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.menu = NSMenu()
        self.statusMenuItem = NSMenuItem(title: "Status: Starting…", action: nil, keyEquivalent: "")
        self.statusMenuItem.isEnabled = false

        super.init()

        if let button = statusItem.button {
            if #available(macOS 11.0, *) {
                button.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Friday")
            } else {
                button.title = "⚡"
            }
        }

        buildMenu()
        statusItem.menu = menu
    }

    private func buildMenu() {
        let titleItem = NSMenuItem(title: "Friday Voice Agent", action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)
        menu.addItem(statusMenuItem)
        menu.addItem(NSMenuItem.separator())

        let toggleNotch = NSMenuItem(title: "Show Notch Overlay", action: #selector(toggleNotchTapped(_:)), keyEquivalent: "")
        toggleNotch.target = self
        toggleNotch.state = .on
        menu.addItem(toggleNotch)
        self.toggleNotchMenuItem = toggleNotch

        let toggleAutoHide = NSMenuItem(title: "Auto-hide when Idle", action: #selector(toggleAutoHideTapped(_:)), keyEquivalent: "")
        toggleAutoHide.target = self
        toggleAutoHide.state = .off
        menu.addItem(toggleAutoHide)
        self.toggleAutoHideMenuItem = toggleAutoHide

        menu.addItem(NSMenuItem.separator())

        let settingsItem = NSMenuItem(title: "Settings…", action: #selector(openSettingsTapped(_:)), keyEquivalent: ",")
        settingsItem.target = self
        menu.addItem(settingsItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "Quit Friday", action: #selector(quitTapped(_:)), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    func updateMenu(stripVisible: Bool, autoHideWhenIdle: Bool) {
        toggleNotchMenuItem?.state = stripVisible ? .on : .off
        toggleAutoHideMenuItem?.state = autoHideWhenIdle ? .on : .off
    }

    func setStatus(_ status: String) {
        statusMenuItem.title = "Status: \(status.capitalized)"
        if let button = statusItem.button {
            if #available(macOS 11.0, *) {
                let symbolName: String
                switch status {
                case "listening":
                    symbolName = "mic.fill"
                case "executing", "routing":
                    symbolName = "hourglass"
                case "failed":
                    symbolName = "exclamationmark.triangle"
                default:
                    symbolName = "waveform"
                }
                button.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: "Friday: \(status)")
            }
        }
    }

    @objc private func toggleNotchTapped(_ sender: NSMenuItem) {
        let newState = sender.state != .on
        sender.state = newState ? .on : .off
        onToggleNotch?(newState)
    }

    @objc private func toggleAutoHideTapped(_ sender: NSMenuItem) {
        let newState = sender.state != .on
        sender.state = newState ? .on : .off
        onToggleAutoHide?(newState)
    }

    @objc private func openSettingsTapped(_ sender: Any?) {
        onOpenSettings?()
    }

    @objc private func quitTapped(_ sender: Any?) {
        onQuit?()
    }
}
