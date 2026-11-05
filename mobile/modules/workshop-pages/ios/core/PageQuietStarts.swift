import WebKit

/// A page's video may start by itself, but its sound starts only from a tap
/// or click. A start, or an unmute while playing, that no tap is behind is
/// muted rather than refused, so the picture plays on silently; a player the
/// user has started or unmuted keeps its sound. Every frame, from document
/// start, in the page's own world (its calls to `play` are what it wraps).
/// WebKit's own `mediaTypesRequiringUserActionForPlayback` refuses the whole
/// start instead, which leaves the video paused, so it is not used.
enum PageQuietStarts {
    static let source = """
    (() => {
      const M = HTMLMediaElement.prototype;
      if (M.__workshopQuietStarts) return;
      Object.defineProperty(M, "__workshopQuietStarts", { value: true });
      const heard = new WeakSet();
      const asked = () => !!(navigator.userActivation && navigator.userActivation.isActive);
      const quiet = (m) => {
        if (asked()) { heard.add(m); return; }
        if (!heard.has(m) && !m.muted && m.srcObject == null) m.muted = true;
      };
      const play = M.play;
      M.play = function () { quiet(this); return play.apply(this, arguments); };
      const on = (type, fn) => addEventListener(type, (e) => {
        if (e.target instanceof HTMLMediaElement) fn(e.target);
      }, true);
      on("play", quiet);
      on("volumechange", (m) => { if (!m.muted && !m.paused) quiet(m); });
    })();
    """

    @MainActor static var script: WKUserScript {
        WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: false, in: .page)
    }
}
