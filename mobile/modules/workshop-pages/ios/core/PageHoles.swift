import CoreGraphics

/// Where the web UI draws over one page view, in its superview's coordinates,
/// and so which clicks and touches it takes from the page (`PageWebView`).
/// The page is drawn under the web UI, so these decide input only. Two sets kept
/// apart: the app's own cutouts and the shell's overlays (the same for every
/// page). A modal set (a menu or dialog is open) takes every click, as a page
/// does under an open menu; otherwise only those inside a hole.
struct PageHoles {
    var cutouts: [CGRect] = []
    var cutoutsModal = true
    var overlays: [CGRect] = []
    var overlaysModal = false

    var all: [CGRect] { cutouts + overlays }

    /// Whether the web UI takes a click or touch at `point`.
    func shellTakes(_ point: CGPoint) -> Bool {
        if !cutouts.isEmpty && cutoutsModal { return true }
        if !overlays.isEmpty && overlaysModal { return true }
        return all.contains { $0.contains(point) }
    }
}
