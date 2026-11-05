import Foundation

extension PageView {
    /// What a page reports of its sound: nothing where WebKit cannot say.
    func audioState() -> [String: Any?] {
        guard PageAudio.supported(webView) else { return [:] }
        return ["playingAudio": PageAudio.playing(webView), "muted": mutedAudio]
    }

    /// Tells `onAudio` that WebKit's report of the page's sound changed.
    nonisolated func audioMoved() {
        DispatchQueue.main.async { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.onAudio?(self)
            }
        }
    }

    func setMuted(_ muted: Bool) throws {
        guard PageAudio.supported(webView) else { throw PageError.fail("This build cannot mute a page") }
        mutedAudio = muted
        PageAudio.mute(webView, muted || mediaHeld)
        emit("pageState", state())
    }

    /// Draws the page or not, and holds its media or lets it play, as
    /// `PageShowing` decides. A held page's sound and video are paused and
    /// cannot start, and its sound is muted too; letting it go again lets it
    /// play, with what was playing left paused.
    func apply(drawn: Bool, plays: Bool) {
        if webView.isHidden == drawn { webView.isHidden = !drawn }
        guard mediaHeld != !plays else { return }
        mediaHeld = !plays
        PageAudio.hold(webView, mediaHeld, muted: mutedAudio)
        emit("pageState", state())
        onAudio?(self)
    }
}
