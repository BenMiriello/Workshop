import Foundation
import WebKit

/// Whether a page view is making sound, and keeping it quiet. The calls are
/// WebKit's own, the ones Safari's tab speaker uses, and not in its public
/// interface; they are looked up by name and used only where WebKit answers
/// them, so a WebKit without them reports nothing rather than guessing.
enum PageAudio {
    static let playingKey = "_isPlayingAudio"
    private static let muteSelector = NSSelectorFromString("_setPageMuted:")
    private static let mutedAudio: UInt = 1

    /// Whether this WebKit can tell sound and silence a page.
    static func supported(_ webView: WKWebView) -> Bool {
        webView.responds(to: NSSelectorFromString(playingKey)) && webView.responds(to: muteSelector)
    }

    /// Whether the page has audio playing, muted or not.
    static func playing(_ webView: WKWebView) -> Bool {
        (webView.value(forKey: playingKey) as? Bool) ?? false
    }

    /// Silences the page's sound, or lets it through; false when WebKit has no such call.
    @discardableResult
    static func mute(_ webView: WKWebView, _ muted: Bool) -> Bool {
        guard let method = webView.method(for: muteSelector), webView.responds(to: muteSelector) else { return false }
        typealias Call = @convention(c) (AnyObject, Selector, UInt) -> Void
        unsafeBitCast(method, to: Call.self)(webView, muteSelector, muted ? mutedAudio : 0)
        return true
    }

    /// Holds every sound and video in a page that is off screen, or lets them
    /// go again. Holding pauses them and suspends playback, which keeps the
    /// page from starting any; letting go lifts the suspension and restarts
    /// nothing, so what was playing stays paused until it is played. The mute
    /// covers sound neither reaches.
    @MainActor
    static func hold(_ webView: WKWebView, _ held: Bool, muted: Bool) {
        if held { webView.pauseAllMediaPlayback() }
        webView.setAllMediaPlaybackSuspended(held)
        mute(webView, held || muted)
    }

    /// Stops every sound and video in a page that is closing. Taking the view
    /// off screen does not stop them: WebKit plays on until the view is freed,
    /// so the media is halted here and the page swapped for a blank one.
    @MainActor
    static func silence(_ webView: WKWebView) {
        mute(webView, true)
        webView.pauseAllMediaPlayback()
        webView.setAllMediaPlaybackSuspended(true)
        webView.closeAllMediaPresentations(completionHandler: nil)
        webView.loadHTMLString("", baseURL: nil)
    }
}

/// Calls `changed` when a web view starts or stops playing audio.
final class PageAudioWatch: NSObject {
    private weak var webView: WKWebView?
    private let changed: () -> Void

    init?(_ webView: WKWebView, changed: @escaping () -> Void) {
        guard webView.responds(to: NSSelectorFromString(PageAudio.playingKey)) else { return nil }
        self.webView = webView
        self.changed = changed
        super.init()
        webView.addObserver(self, forKeyPath: PageAudio.playingKey, options: [], context: nil)
    }

    deinit { stop() }

    func stop() {
        webView?.removeObserver(self, forKeyPath: PageAudio.playingKey)
        webView = nil
    }

    override func observeValue(
        forKeyPath keyPath: String?, of object: Any?, change: [NSKeyValueChangeKey: Any]?,
        context: UnsafeMutableRawPointer?
    ) {
        changed()
    }
}
