import Foundation
import WebKit

/// A page's sound and video belong to the page the person is on, so leaving
/// it pauses every one of them at once, before the next page has answered.
/// WebKit keeps the page being left running and playing until the next one
/// arrives, which for a slow or dead site is a minute later or never.
///
/// A page is left by any load in its main frame (a link, an address, Back,
/// Forward, a reload, an app's navigate) and by a move through its own
/// history, which is how a site that changes page without loading one does
/// it. WebKit's pause reaches the players such a site keeps alive off the page
/// too. An address a page rewrites in place (a feed naming the item scrolled
/// to) keeps its history entry, and a jump to a place in the same page keeps
/// its document, so neither one leaves the page.
@MainActor
final class PageLeaveWatch {
    private weak var webView: WKWebView?
    private var entry: ObjectIdentifier?
    private var entryURL: URL?
    private var watch: NSKeyValueObservation?

    init(_ webView: WKWebView) {
        self.webView = webView
        watch = webView.observe(\.url) { [weak self] _, _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.addressChanged() } }
        }
    }

    /// The main frame began loading another document.
    func loadStarted() {
        webView?.pauseAllMediaPlayback()
    }

    /// The main frame shows the document it loaded, on an entry of its own.
    func committed() {
        note()
    }

    func stop() {
        watch?.invalidate()
        watch = nil
    }

    private func addressChanged() {
        guard let current = webView?.backForwardList.currentItem else { return }
        guard ObjectIdentifier(current) != entry else {
            entryURL = current.url
            return
        }
        let left = entry != nil && !Self.samePage(entryURL, current.url)
        note()
        if left { webView?.pauseAllMediaPlayback() }
    }

    private func note() {
        let current = webView?.backForwardList.currentItem
        entry = current.map(ObjectIdentifier.init)
        entryURL = current?.url
    }

    /// Whether two addresses are places in one page: the same but for the fragment.
    nonisolated static func samePage(_ a: URL?, _ b: URL?) -> Bool {
        guard let a, let b else { return false }
        func page(_ u: URL) -> String {
            var c = URLComponents(url: u, resolvingAgainstBaseURL: false)
            c?.fragment = nil
            return c?.string ?? u.absoluteString
        }
        return page(a) == page(b)
    }
}
