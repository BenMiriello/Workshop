import AppKit
import WebKit

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageAudio, PageLeave and PageFailure, once per case, since
// only a process's first page starts playing in the build account:
//   rules    the pure parts: which errors are failures, what a failure says
//   replace  a page rewriting its address in place keeps playing
//   push     a page moving through its own history is paused
//   pop      so is one going back through it
//   load     a page whose next page never answers is paused at once
//   failure  a failed load lands on the failure page, with Back to the page left

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let mode = CommandLine.arguments.dropFirst().first ?? "rules"
var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL (\(mode)): \(what)")
    }
}

func pump(until done: () -> Bool, within seconds: Double) -> Bool {
    let end = Date().addingTimeInterval(seconds)
    while !done() && Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    return done()
}

/// One second of silence, 8 kHz 16-bit mono, as a WAV data address. WebKit
/// counts it as playing audio all the same, and the build runs on the
/// operator's Mac, where a tone would come out of the speakers.
func silentClip() -> String {
    let rate = 8000
    let samples = Data(count: rate * 2)
    var wav = Data("RIFF".utf8)
    func put<T: FixedWidthInteger>(_ n: T) { withUnsafeBytes(of: n.littleEndian) { wav.append(contentsOf: $0) } }
    put(UInt32(36 + samples.count))
    wav.append(contentsOf: Data("WAVEfmt ".utf8))
    put(UInt32(16)); put(UInt16(1)); put(UInt16(1)); put(UInt32(rate)); put(UInt32(rate * 2)); put(UInt16(2)); put(UInt16(16))
    wav.append(contentsOf: Data("data".utf8))
    put(UInt32(samples.count))
    wav.append(samples)
    return "data:audio/wav;base64," + wav.base64EncodedString()
}

/// What `script` answers in the page, asked until it does: a page with no
/// sound device here answers some calls late.
func ask(_ web: WKWebView, _ script: String) -> Any? {
    for _ in 0..<5 {
        var answer: Any?
        var done = false
        web.evaluateJavaScript(script) { value, _ in
            answer = value
            done = true
        }
        if pump(until: { done }, within: 2), let answer { return answer }
    }
    return nil
}

/// Pages served as a site's are (`page:`, the body in `pages` by host, each
/// load a history entry, which a page loaded from a string never is), and
/// loads that never answer (`hang:`) or fail at once as timed out (`fail:`).
final class Scheme: NSObject, WKURLSchemeHandler {
    var pages: [String: String] = [:]
    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return }
        if url.scheme == "fail" { return task.didFailWithError(URLError(.timedOut)) }
        guard url.scheme == "page" else { return }
        let body = Data((pages[url.host ?? ""] ?? "").utf8)
        task.didReceive(URLResponse(url: url, mimeType: "text/html", expectedContentLength: body.count, textEncodingName: "utf-8"))
        task.didReceive(body)
        task.didFinish()
    }
    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}

/// Hands the page view's loads to the watch, as PageView does.
final class Loads: NSObject, WKNavigationDelegate {
    var watch: PageLeaveWatch?
    var failed: Error?
    var finished = 0
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        MainActor.assumeIsolated { watch?.loadStarted() }
    }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        MainActor.assumeIsolated { watch?.committed() }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { finished += 1 }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed = error
    }
}

func rules() {
    expect(!PageFailure.failed(URLError(.cancelled)), "a stopped load is not a failure")
    expect(!PageFailure.failed(NSError(domain: "WebKitErrorDomain", code: 102)), "a download is not a failure")
    expect(PageFailure.failed(URLError(.timedOut)), "a timed out load is a failure")
    expect(PageFailure.reason(URLError(.timedOut), host: "a.test") == "a.test didn't answer.", "a timeout names the site")
    let page = PageFailure.page(URLError(.timedOut), at: URL(string: "https://a.test/x")!)
    expect(page.contains("<title>Couldn't open a.test</title>"), "the failure page is titled for the site")
    expect(PageLeaveWatch.samePage(URL(string: "https://a.test/p#one"), URL(string: "https://a.test/p#two")),
           "places in one page are one page")
    expect(!PageLeaveWatch.samePage(URL(string: "https://a.test/p"), URL(string: "https://a.test/q")),
           "another path is another page")
}

MainActor.assumeIsolated {
    if mode == "rules" { return rules() }
    let config = WKWebViewConfiguration()
    config.mediaTypesRequiringUserActionForPlayback = []
    let scheme = Scheme()
    config.setURLSchemeHandler(scheme, forURLScheme: "hang")
    config.setURLSchemeHandler(scheme, forURLScheme: "fail")
    config.setURLSchemeHandler(scheme, forURLScheme: "page")
    scheme.pages = ["first.test": "<p>first</p>", "leave.test": "<audio src=\"\(silentClip())\" autoplay loop></audio>",
                    "pop.test": "<script>history.pushState(null, '', '/second')</script>"
                        + "<audio src=\"\(silentClip())\" autoplay loop></audio>"]
    let frame = NSRect(x: -4000, y: -4000, width: 200, height: 200)
    let web = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
    let loads = Loads()
    web.navigationDelegate = loads
    loads.watch = PageLeaveWatch(web)
    let window = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
    window.contentView?.addSubview(web)
    window.orderFrontRegardless()
    let paused = "document.querySelector('audio').paused"

    if mode == "failure" {
        web.load(URLRequest(url: URL(string: "page://first.test/")!))
        _ = pump(until: { loads.finished > 0 }, within: 10)
        web.load(URLRequest(url: URL(string: "fail://dead.test/page")!))
        _ = pump(until: { loads.failed != nil }, within: 10)
        guard let error = loads.failed else { return expect(false, "the failing load fails") }
        let dead = URL(string: "https://dead.test/page")!
        PageFailure.show(error, at: dead, in: web)
        expect(pump(until: { loads.finished > 1 }, within: 10), "the failure page loads")
        expect(web.url == dead, "the page view is at the address that failed")
        expect(web.title == "Couldn't open dead.test", "the failure page names the site")
        expect((ask(web, "document.body.innerText") as? String)?.contains("dead.test didn't answer.") == true,
               "the failure page says why")
        return
    }

    web.load(URLRequest(url: URL(string: mode == "pop" ? "page://pop.test/" : "page://leave.test/")!))
    guard pump(until: { PageAudio.playing(web) }, within: 10) else {
        return expect(false, "a page with looping audio plays")
    }
    switch mode {
    case "replace":
        _ = ask(web, "history.replaceState(null, '', '/?item=2'), true")
        _ = pump(until: { false }, within: 1)
        expect(web.url?.absoluteString == "page://leave.test/?item=2", "the address was rewritten")
        expect(ask(web, paused) as? Bool == false, "a page rewriting its address keeps playing")
    case "push":
        _ = ask(web, "history.pushState(null, '', '/next'), true")
        _ = pump(until: { false }, within: 0.5)
        expect(pump(until: { !PageAudio.playing(web) }, within: 2), "a page moving through its history stops playing")
        expect(ask(web, paused) as? Bool == true, "its audio is paused")
    case "pop":
        _ = ask(web, "history.back(), true")
        expect(pump(until: { !PageAudio.playing(web) }, within: 2), "a page going back through its own history stops playing")
    case "load":
        web.load(URLRequest(url: URL(string: "hang://next.test/")!))
        expect(pump(until: { !PageAudio.playing(web) }, within: 2), "a page being left stops playing at once")
        expect(ask(web, paused) as? Bool == true, "the page being left, still shown, has its audio paused")
        web.stopLoading()
    default:
        expect(false, "a known case")
    }
    web.removeFromSuperview()
    window.close()
}

if failures > 0 { exit(1) }
print("page leave (\(mode)): ok")
