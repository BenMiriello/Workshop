import Foundation

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with PageCommandRoute: which page view answers a page command.

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

struct View: Equatable {
    let app: String
    let id: String
    var shown = true
}

struct Command {
    let app: String
    let key: String
}

let lists = [
    "app-a": [Command(app: "app-a", key: "w"), Command(app: "app-a", key: "r")],
    "app-b": [Command(app: "app-b", key: "r")],
]

func answer(_ key: String, focused: View? = nil, _ stack: [View],
            foreground: String? = nil) -> (view: View?, app: String, command: Command)? {
    PageCommandRoute.answer(focused: focused, stack: stack, visible: { $0.shown }, foreground: foreground,
                            owner: { $0.app }, commands: { lists[$0] ?? [] }, matches: { $0.key == key })
}

let a1 = View(app: "app-a", id: "1")
let a2 = View(app: "app-a", id: "2")
let b1 = View(app: "app-b", id: "1")
var hiddenA2 = a2
hiddenA2.shown = false
var hiddenB1 = b1
hiddenB1.shown = false

expect(answer("w", focused: a1, [a1, b1])?.view == a1, "the focused page answers under another app's page")
expect(answer("w", focused: b1, [a1, b1]) == nil, "a focused page answers only its app's commands")
expect(answer("w", focused: hiddenA2, [a1, hiddenA2])?.view == hiddenA2, "focus decides, not visibility")
expect(answer("w", [a1, a2])?.view == a2, "with focus elsewhere the topmost visible page answers")
expect(answer("w", [a1, hiddenA2])?.view == a1, "a hidden page is skipped")
expect(answer("w", [a1, hiddenB1])?.view == a1, "an app's hidden page does not put it on screen")
expect(answer("w", [a1, b1]) == nil, "only the app on screen is asked, never one under it")
expect(answer("r", [a1, b1])?.command.app == "app-b", "the app on screen's own command fires")
expect(answer("w", [hiddenA2, hiddenB1]) == nil, "no visible page leaves the key to the shell")
expect(answer("w", []) == nil, "no page leaves the key to the shell")
expect(answer("x", [a1]) == nil, "an unregistered key is the shell's")

func forNoPage(_ found: (view: View?, app: String, command: Command)?, _ app: String) -> Bool {
    guard let found else { return false }
    return found.view == nil && found.app == app
}

expect(forNoPage(answer("w", [], foreground: "app-a"), "app-a"), "with no page view the foreground app answers, for no page")
expect(forNoPage(answer("w", [hiddenA2], foreground: "app-a"), "app-a"), "a hidden page leaves the foreground app to answer")
expect(answer("w", [], foreground: "app-b") == nil, "a foreground app without the key leaves it to the shell")
expect(answer("w", [], foreground: "app-c") == nil, "a foreground app with no commands leaves keys to the shell")
expect(answer("w", [a1], foreground: "app-b")?.view == a1, "a visible page decides over the foreground app")
expect(answer("w", [b1], foreground: "app-a") == nil, "the foreground app is not asked under another app's visible page")
expect(answer("w", focused: a1, [], foreground: "app-b")?.view == a1, "focus decides over the foreground app")
expect(answer("w", focused: hiddenA2, [], foreground: "app-b")?.app == "app-a", "a focused hidden page still decides")

if failures > 0 {
    print("page commands check: \(failures) failed")
    exit(1)
}
print("page commands check: ok")
