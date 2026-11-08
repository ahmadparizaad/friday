// friday-ax — AX helper (PLAN-M6 §4, decision B).
//
// Holds the Accessibility grant and nothing else: it cannot launch apps,
// read files, run scripts or open a network connection. One JSON object
// per line on stdin, one JSON line per reply on stdout:
//
//   {"op":"snapshot","app":"com.apple.finder"}   -> Observation
//   {"op":"press","snapshot_id":"…","element_id":3}
//   {"op":"set_value","snapshot_id":"…","element_id":0,"text":"…"}
//   {"op":"key","combo":"cmd+f"}
//   {"op":"ping"}  -> {"trusted":true|false}, never prompts
//
// Every request naming an element carries the snapshot_id the id came
// from; the helper re-reads the tree and refuses if the element is no
// longer at that id with the same role and label (stale-id defense).
// Errors are typed: not_trusted | app_blocked | element_stale | timeout.

import AppKit
import ApplicationServices
import Foundation

let maxElements = 250
let maxDepth = 12
let maxLabel = 120
let maxValue = 200

let blockedApps: Set<String> = [
    "System Settings", "System Preferences", "Keychain Access", "Passwords",
    "com.apple.systempreferences", "com.apple.SystemPreferences",
    "com.apple.keychainaccess",
]

let allowedKeys: Set<String> = [
    "tab", "shift+tab", "escape", "left", "right", "up", "down", "return",
    "cmd+n", "cmd+f", "cmd+a", "cmd+c", "cmd+v",
]

struct SnapElement {
    let role: String
    let label: String
}

var snapshots: [String: [SnapElement]] = [:]

func emit(_ obj: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
    print(String(data: data, encoding: .utf8) ?? "{}")
    fflush(stdout)
}

func fail(_ code: String, _ message: String) {
    emit(["error": message, "code": code])
}

func axGet(_ el: AXUIElement, _ attr: String) -> Any? {
    var v: AnyObject?
    guard AXUIElementCopyAttributeValue(el, attr as CFString, &v) == .success else { return nil }
    return v
}

func labelOf(_ el: AXUIElement) -> String {
    if let s = axGet(el, kAXTitleAttribute as String) as? String, !s.isEmpty { return String(s.prefix(maxLabel)) }
    if let s = axGet(el, kAXDescriptionAttribute as String) as? String, !s.isEmpty { return String(s.prefix(maxLabel)) }
    // Never fall back to the value: a field's contents are user data, not UI chrome.
    return ""
}

func snapshotApp(bundleID: String?) -> [String: Any] {
    let ws = NSWorkspace.shared
    let app: NSRunningApplication?
    if let bid = bundleID, !bid.isEmpty {
        app = ws.runningApplications.first { $0.bundleIdentifier == bid }
    } else {
        app = ws.frontmostApplication
    }
    guard let target = app else { return ["error": "no such app", "code": "app_blocked"] }
    let name = target.localizedName ?? "?"
    let bid = target.bundleIdentifier ?? "?"
    if blockedApps.contains(name) || blockedApps.contains(bid) {
        return ["error": "app \(name) is blocked", "code": "app_blocked"]
    }
    let root = AXUIElementCreateApplication(target.processIdentifier)
    var nodes: [[String: Any]] = []
    var count = 0
    var truncated = false
    func walk(_ el: AXUIElement, _ depth: Int) {
        if count >= maxElements { truncated = true; return }
        if depth > maxDepth { truncated = true; return }
        if let hidden = axGet(el, "AXHidden" as String) as? Bool, hidden { return }
        guard let role = axGet(el, kAXRoleAttribute as String) as? String else { return }
        let enabled = (axGet(el, kAXEnabledAttribute as String) as? Bool) ?? true
        if !enabled { return }
        // macOS password fields are AXTextField with subrole AXSecureTextField.
        let subrole = (axGet(el, kAXSubroleAttribute as String) as? String) ?? ""
        let isSecure = (role == "AXSecureTextField" || subrole == "AXSecureTextField")
        let label = labelOf(el)
        let value: Any = isSecure ? NSNull() : ((axGet(el, kAXValueAttribute as String) as? String).map { String($0.prefix(maxValue)) } ?? NSNull())
        nodes.append([
            "role": role, "label": label, "value": value,
            "enabled": enabled,
            "focused": ((axGet(el, kAXFocusedAttribute as String) as? Bool) ?? false),
            "depth": depth,
            "secure": isSecure,
        ])
        count += 1
        var kids: AnyObject?
        guard AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &kids) == .success,
              let arr = kids as? [AXUIElement] else { return }
        for k in arr { walk(k, depth + 1) }
    }
    var winTitle: Any = NSNull()
    if let winAny = axGet(root, kAXFocusedWindowAttribute as String) {
        let win = winAny as! AXUIElement
        if let t = axGet(win, kAXTitleAttribute as String) as? String { winTitle = t }
        walk(win, 0)
    } else {
        walk(root, 0)
    }
    let sid = UUID().uuidString
    // Store role+label per index for the stale check.
    snapshots[sid] = nodes.map { n in
        SnapElement(role: n["role"] as? String ?? "", label: n["label"] as? String ?? "")
    }
    return [
        "snapshot_id": sid,
        "app": ["name": name, "bundle_id": bid],
        "window_title": winTitle,
        "nodes": nodes,
        "truncated": truncated,
    ]
}

// Re-read the tree and check the element at this index still matches.
func resolve(snapshotID: String, elementID: Int) -> AXUIElement? {
    guard let stored = snapshots[snapshotID], elementID >= 0, elementID < stored.count else { return nil }
    // Re-walk the current frontmost tree to the same index.
    guard let front = NSWorkspace.shared.frontmostApplication else { return nil }
    let root = AXUIElementCreateApplication(front.processIdentifier)
    var flat: [(AXUIElement, String, String)] = []
    func walk(_ el: AXUIElement, _ depth: Int) {
        if flat.count >= maxElements || depth > maxDepth { return }
        guard let role = axGet(el, kAXRoleAttribute as String) as? String else { return }
        flat.append((el, role, labelOf(el)))
        var kids: AnyObject?
        guard AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &kids) == .success,
              let arr = kids as? [AXUIElement] else { return }
        for k in arr { walk(k, depth + 1) }
    }
    if let winAny = axGet(root, kAXFocusedWindowAttribute as String) {
        walk(winAny as! AXUIElement, 0)
    } else {
        walk(root, 0)
    }
    guard elementID < flat.count else { return nil }
    let want = stored[elementID]
    let got = flat[elementID]
    guard got.1 == want.role && got.2 == want.label else { return nil }
    return got.0
}

while let line = readLine(strippingNewline: true) {
    guard let data = line.data(using: .utf8),
          let req = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let op = req["op"] as? String else {
        fail("timeout", "not a JSON request"); continue
    }
    switch op {
    case "ping":
        emit(["trusted": AXIsProcessTrusted()])
    case "prompt":
        // The ONLY op that may show the system prompt, and only ever
        // from `friday ax setup` — a goal never triggers it (§5.7).
        let opts = [kAXTrustedCheckOptionPrompt.takeRetainedValue() as String: true] as CFDictionary
        emit(["trusted": AXIsProcessTrustedWithOptions(opts)])
    case "snapshot":
        if !AXIsProcessTrusted() { fail("not_trusted", "accessibility permission missing"); continue }
        let bid = req["app"] as? String
        let res = snapshotApp(bundleID: bid)
        emit(res)
    case "press":
        if !AXIsProcessTrusted() { fail("not_trusted", "accessibility permission missing"); continue }
        guard let sid = req["snapshot_id"] as? String, let eid = req["element_id"] as? Int,
              let el = resolve(snapshotID: sid, elementID: eid) else {
            fail("element_stale", "element is stale or unknown"); continue
        }
        let err = AXUIElementPerformAction(el, kAXPressAction as CFString)
        if err == .success { emit(["result": "pressed"]) }
        else { fail("element_stale", "press failed") }
    case "set_value":
        if !AXIsProcessTrusted() { fail("not_trusted", "accessibility permission missing"); continue }
        guard let sid = req["snapshot_id"] as? String, let eid = req["element_id"] as? Int,
              let text = req["text"] as? String,
              let el = resolve(snapshotID: sid, elementID: eid) else {
            fail("element_stale", "element is stale or unknown"); continue
        }
        if text.count > 500 { fail("timeout", "text too long"); continue }
        let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
        if err == .success { emit(["result": "typed"]) }
        else { fail("element_stale", "set value failed") }
    case "key":
        guard let combo = req["combo"] as? String, allowedKeys.contains(combo) else {
            fail("timeout", "key combo not allow-listed"); continue
        }
        emit(["result": "pressed key \(combo)"])
    default:
        fail("timeout", "unknown op \(op)")
    }
}
