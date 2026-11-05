import AppKit
import WebKit

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with ShellWebView: page views are drawn under the shell's web
// view, so it must draw nothing where its document draws nothing, and a click
// it says a page takes must reach the view under it.

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

func pump(until done: () -> Bool, within seconds: Double) -> Bool {
    let end = Date().addingTimeInterval(seconds)
    while !done() && Date() < end { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    return done()
}

/// The alpha and red of `image` at `x`, `y` (from the top left, in points).
func pixel(_ image: NSImage, _ x: Int, _ y: Int) -> (alpha: CGFloat, red: CGFloat)? {
    guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
    let rep = NSBitmapImageRep(cgImage: cg)
    let scale = CGFloat(rep.pixelsWide) / image.size.width
    guard let color = rep.colorAt(x: Int(CGFloat(x) * scale), y: Int(CGFloat(y) * scale)) else { return nil }
    return (color.alphaComponent, color.redComponent)
}

MainActor.assumeIsolated {
    let size = NSSize(width: 200, height: 200)
    let window = NSWindow(contentRect: NSRect(origin: NSPoint(x: -4000, y: -4000), size: size),
                          styleMask: .borderless, backing: .buffered, defer: false)
    let container = NSView(frame: NSRect(origin: .zero, size: size))
    let under = NSView(frame: container.bounds)
    let shell = ShellWebView(frame: container.bounds, configuration: WKWebViewConfiguration())
    container.addSubview(under)
    container.addSubview(shell)
    window.contentView = container
    window.orderFrontRegardless()

    expect((shell.value(forKey: "drawsBackground") as? Bool) == false, "the shell's web view draws no background")

    shell.passes = { $0.x < 100 }
    expect(container.hitTest(NSPoint(x: 50, y: 50)) === under, "a click a page takes reaches the view under the shell")
    let onShell = container.hitTest(NSPoint(x: 150, y: 50))
    expect(onShell != nil && onShell!.isDescendant(of: shell), "every other click is the shell's")
    shell.passes = nil
    let unrouted = container.hitTest(NSPoint(x: 50, y: 50))
    expect(unrouted != nil && unrouted!.isDescendant(of: shell), "with no pages every click is the shell's")

    let page = "<html style=\"background:transparent\"><body style=\"margin:0\">"
        + "<div style=\"width:50px;height:50px;background:rgb(255,0,0)\"></div></body></html>"
    shell.loadHTMLString(page, baseURL: nil)
    _ = pump(until: { !shell.isLoading && shell.estimatedProgress >= 1 }, within: 10)
    var shot: NSImage?
    for _ in 0..<5 where shot == nil {
        var done = false
        shell.takeSnapshot(with: nil) { image, _ in
            shot = image
            done = true
        }
        _ = pump(until: { done }, within: 3)
    }
    guard let shot else {
        expect(false, "the shell's web view gives a snapshot")
        return
    }
    let box = pixel(shot, 25, 25)
    let empty = pixel(shot, 150, 150)
    expect(box.map { $0.alpha > 0.9 && $0.red > 0.9 } ?? false, "what the document draws is drawn")
    expect(empty.map { $0.alpha < 0.1 } ?? false, "where the document draws nothing, the shell is see-through")
    window.close()
}

if failures > 0 { exit(1) }
print("page under: ok")
