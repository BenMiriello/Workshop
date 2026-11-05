import Foundation

/// Where page views go: under the shell's see-through web view, at rects in
/// the shell page's CSS pixels, so whatever the web UI draws shows over them.
@MainActor
protocol PageHost: PageDialogHost {
    /// Throws when there is no shell web view to put pages over.
    func ensureReady() throws
    /// Puts `view` under the shell's web view, over the other pages.
    func attach(_ view: PageWebView)
    func place(_ view: PageWebView, _ rect: PageFrame)
    /// Where `rect` sits in the page views' superview, as `place` puts a view.
    func frame(_ rect: PageFrame) -> CGRect
    func bringToFront(_ view: PageWebView)
}

struct PageFrame {
    var x: Double
    var y: Double
    var width: Double
    var height: Double
}

struct PageProxySetting {
    var host: String
    var port: Int
    var kind: String
}

struct PageOpenRequest {
    var profile: String
    var url: String
    var rect: PageFrame
    var proxy: PageProxySetting?
    var trustCertDER: [String]
    var userAgent: String?
    var contentMode: String?
}
