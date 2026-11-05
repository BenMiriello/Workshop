import Foundation

/// The app whose frame the shell's web UI shows (`shell.foreground`). Until
/// the shell first says, every app counts as shown, so a shell that never
/// reports leaves its pages as their apps set them.
enum PageFront: Equatable {
    case unknown
    case none
    case app(String)

    func shows(_ app: String) -> Bool {
        switch self {
        case .unknown: return true
        case .none: return false
        case .app(let shown): return shown == app
        }
    }
}

/// What the app asked of one page view: hidden or shown, and whether a hidden
/// one keeps its sound and video going.
struct PageShowingAsk {
    var hidden = false
    var keepsPlaying = false
}

/// Whether a page view is drawn and whether its media may play. A page plays
/// only while it is on screen: its app shows it, its app's frame is the one
/// the web UI shows, the shell itself is on screen, and no other page drawn
/// above it covers it whole. The one exception is a page its app hid with
/// `keepsPlaying`, which plays on while its app is in front and the shell is
/// on screen. No app can keep a page drawn or playing once the web UI has
/// moved to something else, and a page an app forgot to hide is held as soon
/// as another page is drawn over it.
///
/// A page WebKit shows full screen is on screen whatever the rest says: its
/// window covers the shell's, which reads to the web UI as the shell going
/// away, so the asks that follow are about a page the person is watching.
enum PageShowing {
    static func drawn(app: String, ask: PageShowingAsk, front: PageFront, fullscreen: Bool = false) -> Bool {
        fullscreen || (front.shows(app) && !ask.hidden)
    }

    static func rule(
        app: String, ask: PageShowingAsk, front: PageFront, shellOnScreen: Bool, fullscreen: Bool = false,
        covered: Bool = false
    ) -> (drawn: Bool, plays: Bool) {
        if fullscreen { return (true, true) }
        let drawn = drawn(app: app, ask: ask, front: front)
        let plays = front.shows(app) && shellOnScreen
            && (ask.hidden ? ask.keepsPlaying : !covered)
        return (drawn, plays)
    }

    /// Whether `frame` lies wholly inside one of `above`, the frames of the
    /// pages drawn over it; a sliver at the edge is not a page being watched.
    static func covered(_ frame: CGRect, by above: [CGRect]) -> Bool {
        guard frame.width > 0, frame.height > 0 else { return false }
        let inner = frame.insetBy(dx: 1, dy: 1)
        return above.contains { $0.contains(inner) }
    }
}
