import Foundation

/// A held page is checked, not trusted. Holding asks WebKit to pause, suspend
/// and mute the page's media, and WebKit says whether the page is still
/// playing audio. A held page that still is gets held again; one still
/// playing after `quietWait` is let go: its view closes with `closedBy`
/// "released", so its app keeps the tab and opens the page again when it is
/// shown. Sound from a page that is not on screen is never left to play on.
extension PagePool {
    static let quietWait: TimeInterval = 2

    func audioChanged(_ page: PageView) {
        guard page.mediaHeld, PageAudio.supported(page.webView), PageAudio.playing(page.webView) else { return }
        PageAudio.hold(page.webView, true, muted: page.mutedAudio)
        let key = PageApps.key(page.app, page.id)
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.quietWait) { [weak self, weak page] in
            MainActor.assumeIsolated {
                guard let self, let page, self.pages[key] === page, page.mediaHeld,
                      PageAudio.playing(page.webView) else { return }
                self.close(page.app, page.id, by: "released")
            }
        }
    }
}
