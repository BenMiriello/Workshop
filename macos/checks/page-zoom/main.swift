import Foundation

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageZoom and PageError: the zoom factors a page view accepts.

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

func clamped(_ factor: Double) -> Double? { try? PageZoom.clamped(factor) }

expect(clamped(1) == 1, "1 stays 1")
expect(clamped(1.1) == 1.1, "1.1 stays 1.1")
expect(clamped(0.3) == 0.3, "the lower end is allowed")
expect(clamped(3) == 3, "the upper end is allowed")
expect(clamped(0.1) == 0.3, "below the range is held to 0.3")
expect(clamped(-2) == 0.3, "a negative factor is held to 0.3")
expect(clamped(8) == 3, "above the range is held to 3")
expect(clamped(.nan) == nil, "NaN is refused")
expect(clamped(.infinity) == nil, "infinity is refused")

if failures > 0 { exit(1) }
print("page zoom: ok")
