import Foundation
import WebKit

/// A main-frame load that fails is shown where it was going, as a browser
/// does: the page view moves to the address that failed and says there why,
/// so Back returns to the page that was left and Try again loads it once
/// more. Left to itself, WebKit keeps showing the page being left and puts
/// its address back, which reads as the page jumping back by itself.
enum PageFailure {
    /// Whether `error` is a load failing, rather than one stopped, replaced
    /// by another, or handed off as a download (WebKit's frame load
    /// interrupted by a policy change, 102) or to a plug-in (204).
    static func failed(_ error: Error) -> Bool {
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return false }
        if ns.domain == "WebKitErrorDomain" && (ns.code == 102 || ns.code == 204) { return false }
        return true
    }

    /// The address a failed load was for, else `asked`.
    static func address(_ error: Error, asked: URL?) -> URL? {
        let info = (error as NSError).userInfo
        if let url = info[NSURLErrorFailingURLErrorKey] as? URL { return url }
        return asked
    }

    /// Why the load failed, in words about the site.
    static func reason(_ error: Error, host: String) -> String {
        let ns = error as NSError
        guard ns.domain == NSURLErrorDomain else { return ns.localizedDescription }
        switch ns.code {
        case NSURLErrorTimedOut: return "\(host) didn't answer."
        case NSURLErrorCannotFindHost: return "There is no site at \(host)."
        case NSURLErrorCannotConnectToHost: return "\(host) refused the connection."
        case NSURLErrorNotConnectedToInternet: return "This device is offline."
        default: return ns.localizedDescription
        }
    }

    /// The failure page for `url`.
    static func page(_ error: Error, at url: URL) -> String {
        let host = url.host ?? url.absoluteString
        let title = html("Couldn't open \(host)")
        return """
        <!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta name="color-scheme" content="dark light"><title>\(title)</title>
        <style>
        body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
          font: 15px -apple-system, system-ui, sans-serif; background: Canvas; color: CanvasText; }
        main { max-width: 30em; padding: 24px; }
        h1 { font-size: 20px; margin: 0 0 8px; }
        p { margin: 0 0 16px; color: GrayText; overflow-wrap: anywhere; }
        button { font: inherit; padding: 6px 14px; }
        </style></head><body><main>
        <h1>\(title)</h1><p>\(html(reason(error, host: host)))</p>
        <button id="again">Try again</button>
        </main><script>
        document.getElementById("again").onclick = () => location.replace(\(script(url.absoluteString)));
        </script></body></html>
        """
    }

    /// Shows the failure page for `url` in `webView`.
    @MainActor
    static func show(_ error: Error, at url: URL, in webView: WKWebView) {
        webView.loadSimulatedRequest(URLRequest(url: url), responseHTML: page(error, at: url))
    }

    private static func html(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
    }

    /// `text` as a JavaScript string that cannot end the script it is in.
    private static func script(_ text: String) -> String {
        var out = "\""
        for unit in text.unicodeScalars {
            switch unit {
            case "\"", "\\", "<", ">", "&", "\u{2028}", "\u{2029}": out += String(format: "\\u%04x", unit.value)
            default: out.unicodeScalars.append(unit)
            }
        }
        return out + "\""
    }
}
