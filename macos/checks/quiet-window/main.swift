import AppKit
import WebKit

// A key no view takes must not play the alert sound in Workshop's window
// (QuietWindow.swift). The same keys on a plain NSWindow must, or this check
// could not see a beep at all. Run with sound-hook.dylib inserted
// (scripts/lib/macos-build-mac.sh); hook.c counts the sounds.

guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "ws_sound_count") else {
    print("quiet window check: FAIL, the sound hook is not loaded")
    exit(1)
}
let sounds = symbol.assumingMemoryBound(to: Int32.self)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

func pump(_ seconds: Double) { RunLoop.current.run(until: Date().addingTimeInterval(seconds)) }

func press(_ window: NSWindow, _ code: UInt16, _ chars: String) {
    for type in [NSEvent.EventType.keyDown, .keyUp] {
        let event = NSEvent.keyEvent(
            with: type, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, characters: chars, charactersIgnoringModifiers: chars,
            isARepeat: false, keyCode: code)!
        app.sendEvent(event)
    }
}

/// How many alert sounds the keys play on a page with no key handler.
func soundsFromKeys(in window: NSWindow) -> Int {
    let web = WKWebView(frame: window.contentView!.bounds, configuration: WKWebViewConfiguration())
    window.contentView!.addSubview(web)
    window.makeKeyAndOrderFront(nil)
    web.loadHTMLString("<body><p>nothing here takes a key</p>", baseURL: nil)
    pump(1.5)
    window.makeFirstResponder(web)
    let arrow = { (f: Int) in String(UnicodeScalar(f)!) }
    let before = sounds.pointee
    for (code, chars) in [(UInt16(124), arrow(NSRightArrowFunctionKey)), (123, arrow(NSLeftArrowFunctionKey)),
                          (7, "x"), (53, "\u{1b}")] {
        press(window, code, chars)
        pump(0.4)
    }
    return Int(sounds.pointee - before)
}

func makeWindow<W: NSWindow>(_ type: W.Type) -> W {
    W(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
}

let plain = soundsFromKeys(in: makeWindow(NSWindow.self))
let quiet = soundsFromKeys(in: makeWindow(QuietWindow.self))
print("alert sounds from 4 unhandled keys: plain window \(plain), Workshop window \(quiet)")
if plain == 0 {
    print("quiet window check: FAIL, a plain window played nothing, so this check cannot see a beep")
    exit(1)
}
if quiet != 0 {
    print("quiet window check: FAIL, Workshop's window plays the alert for a key nothing took")
    exit(1)
}
print("quiet window check: ok")
