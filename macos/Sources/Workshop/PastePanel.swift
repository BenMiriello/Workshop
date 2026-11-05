import AppKit
import Carbon.HIToolbox
import WebKit

/// Takes keys and Esc without making Workshop the active app, so the app the
/// operator was in stays in front under it.
final class FloatingPanel: NSPanel {
    var onCancel: (() -> Void)?

    override var canBecomeKey: Bool { true }

    override func cancelOperation(_ sender: Any?) {
        onCancel?()
    }
}

/// Cmd+Shift+V in any app opens a panel over it showing the overlay app that
/// keeps copies, through the web UI's /embed/overlay page (the same app and
/// messages the shell opens over its own pages). A clip chosen there is pasted
/// into the app that was in front (`PasteInto`). Offered while the clipboard
/// watch is on "everything".
@MainActor
final class PastePanel: NSObject, WKScriptMessageHandler, NSWindowDelegate {
    /// shared/native-bridge.ts PANEL_CHANNEL, PANEL_OPEN_EVENT, PANEL_CLOSE_EVENT.
    static let channel = "workshopPanel"
    static let openEvent = "workshop:panel-open"
    static let closeEvent = "workshop:panel-close"
    static let size = NSSize(width: 560, height: 600)

    private let home: URL
    private let paste: PasteInto
    private var hotKey: HotKey?
    private var panel: FloatingPanel?
    private var webView: WKWebView?
    private var previous: NSRunningApplication?

    init(home: URL, write: @escaping (String) -> Bool) {
        self.home = home
        paste = PasteInto(write: write)
        super.init()
        hotKey = HotKey(keyCode: kVK_ANSI_V, modifiers: cmdKey | shiftKey) { [weak self] in self?.toggle() }
    }

    var enabled = false {
        didSet {
            hotKey?.enabled = enabled
            if !enabled { hide() }
        }
    }

    private func toggle() {
        if panel?.isVisible == true { hide() } else { show() }
    }

    private func show() {
        let front = NSWorkspace.shared.frontmostApplication
        previous = front
        let into = front?.localizedName ?? ""
        let panel = self.panel ?? makePanel()
        if let webView, webView.url != nil {
            webView.evaluateJavaScript(Self.event(Self.openEvent, into: into))
        } else {
            var parts = URLComponents(url: home, resolvingAgainstBaseURL: false)
            parts?.path = "/embed/overlay"
            parts?.queryItems = [URLQueryItem(name: "into", value: into)]
            if let url = parts?.url { webView?.load(URLRequest(url: url)) }
        }
        place(panel)
        panel.orderFrontRegardless()
        panel.makeKey()
        if let webView { panel.makeFirstResponder(webView) }
    }

    /// The page lets its app go, so nothing in it plays or polls unseen.
    private func hide() {
        guard let panel, panel.isVisible else { return }
        panel.orderOut(nil)
        webView?.evaluateJavaScript(Self.event(Self.closeEvent, into: nil))
    }

    /// A click in another window closes it, as a menu closes.
    func windowDidResignKey(_ notification: Notification) {
        hide()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == home.host,
              let body = message.body as? [String: Any]
        else { return }
        switch body["t"] as? String {
        case "insert":
            guard let text = body["text"] as? String, !text.isEmpty else { return }
            let target = previous
            hide()
            paste.paste(text, into: target)
        case "close":
            hide()
        default:
            break
        }
    }

    private func makePanel() -> FloatingPanel {
        let panel = FloatingPanel(
            contentRect: NSRect(origin: .zero, size: Self.size),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered, defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        panel.delegate = self
        panel.onCancel = { [weak self] in self?.hide() }

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.add(self, name: Self.channel)
        let webView = WKWebView(frame: panel.contentLayoutRect, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.setValue(false, forKey: "drawsBackground")
        panel.contentView = webView
        self.panel = panel
        self.webView = webView
        return panel
    }

    /// Centred on the screen under the pointer, a little above the middle.
    private func place(_ panel: NSPanel) {
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(mouse) }) ?? NSScreen.main
        else { return }
        let area = screen.visibleFrame
        let size = NSSize(width: min(panel.frame.width, area.width), height: min(panel.frame.height, area.height))
        let origin = NSPoint(x: area.midX - size.width / 2, y: area.minY + (area.height - size.height) * 0.6)
        panel.setFrame(NSRect(origin: origin, size: size), display: false)
    }

    private static func event(_ name: String, into: String?) -> String {
        let detail = (try? JSONSerialization.data(withJSONObject: ["into": into ?? ""]))
            .flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        return "window.dispatchEvent(new CustomEvent(\"\(name)\",{detail:\(detail)}))"
    }
}
