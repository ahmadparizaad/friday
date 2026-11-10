// Notch UI — ARCHITECTURE.md §9.
//
// Two surfaces, split by trust tier, not by convenience:
//
//   the 156pt strip  ambient state only. Nothing approvable renders here.
//   a wider panel    every CONFIRM, with arguments shown in full.
//
// The split exists because 156pt cannot hold a verbatim argument, and a
// confirmation that truncates is worse than none: it manufactures the
// appearance of informed consent (§9.2, ASI09).

import AppKit

/// Geometry read from the screen, never hardcoded (§9.1).
struct NotchGeometry {
    let notch: NSRect?
    let screen: NSScreen

    init(screen: NSScreen) {
        self.screen = screen
        guard
            let left = screen.auxiliaryTopLeftArea,
            let right = screen.auxiliaryTopRightArea,
            right.minX > left.maxX
        else {
            // No notch: external display or clamshell. Callers fall back
            // to a menu-bar item rather than drawing into a notch that
            // is not there.
            self.notch = nil
            return
        }
        self.notch = NSRect(
            x: left.maxX,
            y: screen.frame.maxY - left.height,
            width: right.minX - left.maxX,
            height: left.height
        )
    }

    var hasNotch: Bool { notch != nil }

    /// The first display with a notch, or nil (external display only,
    /// or clamshell). Callers fall back to the main screen.
    static func findNotchScreen() -> NSScreen? {
        NSScreen.screens.first { NotchGeometry(screen: $0).hasNotch }
    }

    /// Where the strip sits: the notch itself, or a notch-sized slot at the
    /// top centre of the screen when there is none.
    var stripFrame: NSRect {
        if let notch { return notch }
        let size = NSSize(width: 156, height: 28)
        let frame = screen.frame
        return NSRect(
            x: frame.midX - size.width / 2,
            y: frame.maxY - size.height,
            width: size.width,
            height: size.height
        )
    }
}

enum AgentState: String {
    case idle, listening, routing, executing, failed

    var label: String {
        switch self {
        case .idle: return "●"
        case .listening: return "◉"
        case .routing: return "◐"
        case .executing: return "◓"
        case .failed: return "✕"
        }
    }
}

/// The ambient strip. Non-activating: showing status must never steal
/// focus from the app the user is driving. Clicking it opens Settings;
/// nothing approvable ever renders here.
final class NotchStrip {
    private let panel: NSPanel
    private let label: NSTextField
    private let onClick: () -> Void

    init(geometry: NotchGeometry, onClick: @escaping () -> Void = {}) {
        self.onClick = onClick
        let frame = geometry.stripFrame

        panel = NSPanel(
            contentRect: frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.isMovable = false
        panel.ignoresMouseEvents = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]

        label = NSTextField(labelWithString: AgentState.idle.label)
        label.alignment = .center
        label.textColor = .secondaryLabelColor
        label.font = .monospacedSystemFont(ofSize: 13, weight: .medium)
        label.frame = NSRect(origin: .zero, size: frame.size)

        let content = NSView(frame: NSRect(origin: .zero, size: frame.size))
        content.addSubview(label)
        content.addGestureRecognizer(
            NSClickGestureRecognizer(target: self, action: #selector(clicked))
        )
        panel.contentView = content
    }

    func show() { panel.orderFrontRegardless() }

    func hide() { panel.orderOut(nil) }

    /// Follow the notch when displays are attached, detached or rearranged.
    func reposition(geometry: NotchGeometry) {
        let frame = geometry.stripFrame
        panel.setFrame(frame, display: true)
        label.frame = NSRect(origin: .zero, size: frame.size)
        panel.contentView?.frame = NSRect(origin: .zero, size: frame.size)
    }

    @objc private func clicked() { onClick() }

    func render(_ state: AgentState) {
        label.stringValue = state.label
        label.textColor = state == .failed ? .systemRed : .secondaryLabelColor
    }
}

/// The confirmation panel. Key-taking, dismiss-blocking, and it grows to
/// fit its arguments — there is no ellipsis path (§9.2).
final class ConfirmPanel {
    private var panel: NSPanel?
    /// The decision this panel is showing. A click reports it explicitly
    /// rather than letting the controller read a shared mutable field, so
    /// a verdict cannot be attributed to a panel the user was not looking
    /// at. Defence in depth: the core re-checks the id anyway.
    private var shownDecision: String?
    private let onVerdict: (String, Bool) -> Void
    private let geometry: NotchGeometry

    init(geometry: NotchGeometry, onVerdict: @escaping (String, Bool) -> Void) {
        self.geometry = geometry
        self.onVerdict = onVerdict
    }

    /// Present `description` in full. `verbatim` marks the classes whose
    /// arguments must be read back word for word (§5.4).
    func present(decisionID: String, description: String, verbatim: Bool) {
        dismiss()
        shownDecision = decisionID

        let width: CGFloat = 460
        let inset: CGFloat = 20

        let heading = NSTextField(labelWithString: verbatim ? "Confirm — read carefully" : "Confirm")
        heading.font = .systemFont(ofSize: 13, weight: .semibold)

        // The whole description, wrapped over as many lines as it needs.
        let body = NSTextField(wrappingLabelWithString: description)
        body.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        body.preferredMaxLayoutWidth = width - inset * 2
        body.lineBreakMode = .byWordWrapping
        // Explicitly unbounded: truncation here would defeat the point.
        body.maximumNumberOfLines = 0
        body.cell?.truncatesLastVisibleLine = false

        // Equal weight, no default button: there must be no affordance
        // for approving without reading (§9.2).
        let approve = NSButton(title: "Approve", target: self, action: #selector(approveTapped))
        let deny = NSButton(title: "Deny", target: self, action: #selector(denyTapped))
        for button in [approve, deny] {
            button.bezelStyle = .rounded
            button.keyEquivalent = ""
        }

        let buttons = NSStackView(views: [deny, approve])
        buttons.orientation = .horizontal
        buttons.spacing = 12

        // Past a few thousand characters the body alone is taller than the
        // screen. Truncating is not an option (§9.2), and letting the panel
        // run off-screen is worse — the buttons become unreachable. So the
        // body scrolls while the heading and buttons stay pinned: the whole
        // argument remains readable and the decision remains actionable.
        let maxBody = geometry.screen.visibleFrame.height * 0.6
        let bodyFits = body.fittingSize.height
        let bodyView: NSView
        if bodyFits > maxBody {
            let scroller = NSScrollView()
            scroller.hasVerticalScroller = true
            scroller.drawsBackground = false
            scroller.documentView = body
            scroller.translatesAutoresizingMaskIntoConstraints = false
            scroller.heightAnchor.constraint(equalToConstant: maxBody).isActive = true
            scroller.widthAnchor.constraint(equalToConstant: width - inset * 2).isActive = true
            bodyView = scroller
        } else {
            bodyView = body
        }

        let stack = NSStackView(views: [heading, bodyView, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: inset, left: inset, bottom: inset, right: inset)

        let fitted = stack.fittingSize
        // Never taller than the usable screen: the buttons must stay on it.
        let height = min(max(fitted.height, 120), geometry.screen.visibleFrame.height - 16)

        // Descend from the notch so it reads as one continuous surface,
        // while being a structurally different surface.
        let anchorX = geometry.notch?.midX ?? geometry.screen.frame.midX
        let frame = NSRect(
            x: anchorX - width / 2,
            y: geometry.screen.frame.maxY - geometry.screen.safeAreaInsets.top - height - 8,
            width: width,
            height: height
        )

        let created = NSPanel(
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
        created.collectionBehavior = [.canJoinAllSpaces, .stationary]
        created.contentView = stack
        created.makeKeyAndOrderFront(nil)

        panel = created
    }

    func dismiss() {
        panel?.orderOut(nil)
        panel = nil
        shownDecision = nil
    }

    @objc private func approveTapped() { report(true) }

    @objc private func denyTapped() { report(false) }

    /// Emit a verdict naming the decision this panel was showing. If the
    /// panel has already been dismissed there is nothing to answer, so a
    /// late or double click is dropped rather than answering whatever is
    /// current.
    private func report(_ approved: Bool) {
        guard let decision = shownDecision else { return }
        dismiss()
        onVerdict(decision, approved)
    }
}
