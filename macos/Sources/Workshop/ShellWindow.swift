import AppKit
import WebKit

/// A view whose y axis runs down, like the web page's, so rects the page
/// reports in CSS pixels place native views directly.
final class FlippedView: NSView {
    /// A look at each command key before any view inside; true when taken.
    var takeKey: ((NSEvent) -> Bool)?

    override var isFlipped: Bool { true }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if takeKey?(event) == true { return true }
        return super.performKeyEquivalent(with: event)
    }
}

/// The one Workshop window: Workshop's web UI in a see-through WKWebView
/// filling a flipped container, with the page views under it. The container
/// paints the web UI's own background color, which its document gives up
/// wherever a page shows.
@MainActor
final class ShellWindow: NSWindowController, NSWindowDelegate {
    let webView: ShellWebView
    let container = FlippedView()
    private let home: URL
    private let bridge: Bridge
    private let web: ShellWebDelegate
    private var pages: MacPages?
    private var clipboard: ClipboardBridge?
    private var pastePanel: PastePanel?
    let commands = PageCommands()

    init(url: URL) {
        home = url
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.preferences.isElementFullscreenEnabled = true
        config.mediaTypesRequiringUserActionForPlayback = []
        bridge = Bridge(controller: config.userContentController)
        webView = ShellWebView(frame: .zero, configuration: config)
        web = ShellWebDelegate(home: url)

        let window = QuietWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Workshop"
        window.minSize = NSSize(width: 360, height: 480)
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        super.init(window: window)

        container.autoresizingMask = [.width, .height]
        window.contentView = container
        webView.frame = container.bounds
        webView.autoresizingMask = [.width, .height]
        webView.allowsBackForwardNavigationGestures = true
        webView.isInspectable = true
        webView.navigationDelegate = web
        webView.uiDelegate = web
        container.addSubview(webView)
        bridge.webView = webView
        commands.shell = webView
        container.takeKey = { [weak self, weak commands = commands] event in
            if self?.takesBesideKey(event) == true { return true }
            return commands?.handle(event) ?? false
        }
        let pages = MacPages(container: container, shell: webView)
        self.pages = pages
        webView.passes = { [weak pages] point in pages?.pool.pageTaking(point) != nil }
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        bridge.onAppearance = { [weak container] color in container?.layer?.backgroundColor = color }
        PagesBridge.install(pages.pool, commands: commands, on: bridge)
        commands.emit = { [weak bridge] topic, payload in bridge?.emit(topic, payload) }
        // The app whose frame the web UI shows (shared/native-bridge.ts `shell`).
        bridge.handle("shell", "foreground") { [weak commands = commands, weak pages] a, _ in
            let app = try (a.first as? String).map { try PageApps.check($0) }
            commands?.foreground = app
            pages?.pool.setForeground(app)
            return nil
        }
        MicBridge.install(on: bridge)
        let clipboard = ClipboardBridge(reporter: CopyReporter(origin: url))
        clipboard.install(on: bridge)
        self.clipboard = clipboard
        let pastePanel = PastePanel(home: url) { [weak clipboard] in clipboard?.write($0) ?? false }
        self.pastePanel = pastePanel
        clipboard.onMode = { [weak pastePanel] in pastePanel?.enabled = $0 == "everything" }
        bridge.onDocument = { [weak pages, weak commands = commands] in
            pages?.pool.closeAll(nil)
            commands?.clear()
        }

        window.delegate = self
        window.center()
        window.setFrameAutosaveName("Workshop")
        webView.load(URLRequest(url: url))
    }

    required init?(coder: NSCoder) { nil }

    /// A window that is minimized, hidden with the app or fully covered is
    /// off screen, and so are its page views: their media is held.
    func windowDidChangeOcclusionState(_ notification: Notification) {
        guard let window else { return }
        pages?.pool.setShellOnScreen(window.occlusionState.contains(.visible))
    }

    /// The web UI owns find; this is how the menu's Cmd+F reaches it.
    func findInPage() {
        webView.evaluateJavaScript("window.dispatchEvent(new Event('workshop:find'))")
    }

    /// Ctrl+I and Ctrl+backtick, looked at before any view so a page view with
    /// focus cannot keep them.
    private func takesBesideKey(_ event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection([.command, .shift, .option, .control])
        guard event.type == .keyDown, flags == .control,
              let key = event.charactersIgnoringModifiers, key == "i" || key == "`"
        else { return false }
        toggleBeside()
        return true
    }

    /// Ctrl+I: a menu item, so it is taken whichever view has focus, a page
    /// view included; the web UI owns the chat beside the page.
    func toggleBeside() {
        webView.evaluateJavaScript("window.dispatchEvent(new Event('workshop:beside'))")
    }

    func reloadWorkshop() {
        if webView.url == nil {
            webView.load(URLRequest(url: home))
        } else {
            webView.reload()
        }
    }

    /// Asked once, when a newer build has replaced this app on disk.
    func offerRelaunch() {
        let alert = NSAlert()
        alert.messageText = "Workshop was updated"
        alert.informativeText = "Relaunch now to use it, or later from the Workshop menu."
        alert.addButton(withTitle: "Relaunch")
        alert.addButton(withTitle: "Later")
        guard let window else { return }
        alert.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn { UpdateWatch.relaunch() }
        }
    }

}
