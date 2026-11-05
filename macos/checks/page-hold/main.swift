import AppKit
import WebKit

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageAudio and PageShowing: a page view is let play only on
// screen, and a held page stops playing, is muted, and stays paused when let
// go. Playing again after a hold is not measured: the build account has no
// sound device, so a page's clock never moves and its scripts answer late. A
// page of its own, since only a process's first page starts playing here.

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

func plays(_ ask: PageShowingAsk, _ front: PageFront, _ onScreen: Bool = true) -> Bool {
    PageShowing.rule(app: "a", ask: ask, front: front, shellOnScreen: onScreen).plays
}
let hidden = PageShowingAsk(hidden: true, keepsPlaying: false)
let keeps = PageShowingAsk(hidden: true, keepsPlaying: true)
expect(plays(PageShowingAsk(), .app("a")), "a shown page of the app in front plays")
expect(plays(PageShowingAsk(), .unknown), "before the shell says, a shown page plays")
expect(!plays(hidden, .app("a")), "a hidden page is held")
expect(plays(keeps, .app("a")), "a hidden page kept playing plays while its app is in front")
expect(!plays(keeps, .app("b")), "another app in front holds every page, kept or not")
expect(!plays(PageShowingAsk(), PageFront.none), "no app in front holds every page")
expect(!plays(PageShowingAsk(), .app("a"), false), "a shell off screen holds every page")
expect(!PageShowing.rule(app: "a", ask: PageShowingAsk(), front: .app("b"), shellOnScreen: true).drawn,
       "another app in front hides the page")
let full = PageShowing.rule(app: "a", ask: hidden, front: PageFront.none, shellOnScreen: false, fullscreen: true)
expect(full.drawn && full.plays, "a page in full screen is drawn and plays, whatever the shell asks")
let under = PageShowing.rule(app: "a", ask: PageShowingAsk(), front: .app("a"), shellOnScreen: true, covered: true)
expect(under.drawn && !under.plays, "a page another page covers whole is held")
let page = CGRect(x: 0, y: 80, width: 390, height: 700)
expect(PageShowing.covered(page, by: [page]), "a page under one of the same place is covered")
expect(!PageShowing.covered(page, by: [CGRect(x: 40, y: 200, width: 300, height: 300)]),
       "a pop-up floating over part of a page leaves it on screen")
expect(!PageShowing.covered(page, by: []), "a page with none above it is not covered")

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

/// Whether the page's audio element reads paused, asked until it answers.
/// A page with no sound device here answers some calls late or never.
func audioPaused(_ web: WKWebView) -> Bool? {
    for _ in 0..<5 {
        var answer: Bool?
        var done = false
        web.evaluateJavaScript("document.querySelector('audio').paused") { value, _ in
            answer = value as? Bool
            done = true
        }
        if pump(until: { done }, within: 2), let answer { return answer }
    }
    return nil
}

MainActor.assumeIsolated {
    let config = WKWebViewConfiguration()
    config.mediaTypesRequiringUserActionForPlayback = []
    let frame = NSRect(x: -4000, y: -4000, width: 200, height: 200)
    let held = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
    let window = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
    window.contentView?.addSubview(held)
    window.orderFrontRegardless()
    held.loadHTMLString("<audio src=\"\(silentClip())\" autoplay loop></audio>", baseURL: nil)
    guard pump(until: { PageAudio.playing(held) }, within: 10) else {
        expect(false, "a page with looping audio plays")
        return
    }
    func mutedState() -> UInt { (held.value(forKey: "_mediaMutedState") as? NSNumber)?.uintValue ?? 99 }
    PageAudio.hold(held, true, muted: false)
    expect(pump(until: { !PageAudio.playing(held) }, within: 2), "a held page stops playing")
    expect(audioPaused(held) == true, "a held page's audio is paused")
    expect(mutedState() == 1, "a held page is muted")
    PageAudio.hold(held, false, muted: false)
    expect(mutedState() == 0, "a page let go is not muted")
    expect(audioPaused(held) == true, "a page let go stays paused")
    PageAudio.silence(held)
    held.removeFromSuperview()
    window.close()
}

if failures > 0 { exit(1) }
print("page hold: ok")
