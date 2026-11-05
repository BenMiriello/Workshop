import AppKit
import WebKit

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageAudio: this macOS still answers the WebKit calls a page
// view's sound depends on, the mute reaches the page, and a page silenced on
// closing stops playing. Holding a page is checks/page-hold.

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

let web = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
expect(PageAudio.supported(web), "WebKit answers the sound calls")
expect(!PageAudio.playing(web), "a page with nothing loaded is not playing")

func mutedState() -> UInt { (web.value(forKey: "_mediaMutedState") as? NSNumber)?.uintValue ?? 99 }

expect(mutedState() == 0, "a new page is not muted")
expect(PageAudio.mute(web, true), "muting is accepted")
expect(mutedState() == 1, "the page's audio is muted")
expect(PageAudio.mute(web, false), "unmuting is accepted")
expect(mutedState() == 0, "the page's audio is let through")

let watch = PageAudioWatch(web) {}
expect(watch != nil, "the playing state can be watched")
watch?.stop()
watch?.stop()

func pump(until done: () -> Bool, within seconds: Double) -> Bool {
    let end = Date().addingTimeInterval(seconds)
    while !done() && Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    return done()
}

/// One second of a 440 Hz tone, 8 kHz 16-bit mono, as a WAV data address.
func tone() -> String {
    let rate = 8000
    var samples = Data()
    for i in 0..<rate {
        let value = Int16(sin(Double(i) * 2 * .pi * 440 / Double(rate)) * 8000)
        withUnsafeBytes(of: value.littleEndian) { samples.append(contentsOf: $0) }
    }
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

MainActor.assumeIsolated {
    let config = WKWebViewConfiguration()
    config.mediaTypesRequiringUserActionForPlayback = []
    let frame = NSRect(x: -4000, y: -4000, width: 200, height: 200)
    let player = WKWebView(frame: NSRect(origin: .zero, size: frame.size), configuration: config)
    let window = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
    window.contentView?.addSubview(player)
    window.orderFrontRegardless()
    player.loadHTMLString("<audio src=\"\(tone())\" autoplay loop></audio>", baseURL: nil)
    guard pump(until: { PageAudio.playing(player) }, within: 10) else {
        expect(false, "a page with a looping tone plays")
        return
    }
    PageAudio.silence(player)
    player.removeFromSuperview()
    expect(pump(until: { !PageAudio.playing(player) }, within: 2), "a page silenced on closing stops playing")
    window.close()
}

if failures > 0 { exit(1) }
print("page audio: ok")
