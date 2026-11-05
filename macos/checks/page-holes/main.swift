import CoreGraphics
import Foundation

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageHoles: where the web UI draws over a page view, which
// clicks and touches it takes. A bar the shell keeps up (dictation) takes only
// its own area; an open menu or dialog, the app's or the shell's, takes all.

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

let bar = CGRect(x: 100, y: 500, width: 300, height: 40)
let inBar = CGPoint(x: 150, y: 520)
let onPage = CGPoint(x: 150, y: 100)

var holes = PageHoles()
expect(!holes.shellTakes(onPage), "a page with no holes keeps every click")
expect(holes.all.isEmpty, "a page with no holes is whole")

holes.overlays = [bar]
expect(holes.shellTakes(inBar), "a click on the shell's bar goes to the shell")
expect(!holes.shellTakes(onPage), "a click beside the shell's bar stays with the page")

holes.overlaysModal = true
expect(holes.shellTakes(onPage), "while the shell has a menu open, every click goes to the shell")

holes = PageHoles(cutouts: [bar])
expect(holes.shellTakes(onPage), "an app's cutouts are modal unless it says otherwise")
holes.cutoutsModal = false
expect(!holes.shellTakes(onPage) && holes.shellTakes(inBar), "an app's non-modal cutout takes only its own area")

holes.overlays = [CGRect(x: 0, y: 0, width: 50, height: 50)]
expect(holes.all.count == 2, "the app's holes and the shell's are both cut")
holes.cutouts = []
expect(holes.all.count == 1 && holes.shellTakes(CGPoint(x: 10, y: 10)),
       "clearing the app's cutouts leaves the shell's in place")

if failures > 0 {
    print("page holes: \(failures) failed")
    exit(1)
}
print("page holes: ok")
