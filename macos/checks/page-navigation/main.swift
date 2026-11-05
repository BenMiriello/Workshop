import Foundation

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with the page core's navigation rules: sites, hops, cleaning,
// which navigations count as jumps to cancel, and which responses are files
// for the app.

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

let sites = PageSites(["com", "uk", "co.uk", "github.io", "*.ck", "!www.ck"])
expect(sites.site("a.b.example.co.uk") == "example.co.uk", "a subdomain under a two-label suffix")
expect(sites.site("Example.COM.") == "example.com", "case and a trailing dot")
expect(sites.site("x.github.io") != sites.site("y.github.io"), "two pages of a private suffix are two sites")
expect(sites.site("a.foo.ck") == "a.foo.ck", "a wildcard suffix")
expect(sites.site("www.ck") == "www.ck", "an exception to a wildcard")
expect(sites.site("127.0.0.1") == "127.0.0.1", "an address is its own site")
expect(PageSites().site("a.b.example.net") == "example.net", "without a list, the last two labels")

func rules(_ d: [String: Any]) -> PageNavigationRules {
    do { return try PageNavigationRules(d) } catch { fatalError("rules did not read: \(error)") }
}

let hopRules = rules([
    "hops": [
        ["include": ["*://go.example.net/out?*"], "param": "url"],
        ["include": ["*://*.b64.net/*"], "param": "u", "base64": true],
        ["include": ["*://*.awstrack.me/L0/*"], "pathPattern": "^/L0/([^/]+)/.*$"],
        ["include": ["*://y2u.be/*"], "pathPattern": "^/([^/]+)$", "template": "https://www.youtube.com/watch?v=$1"],
        ["include": ["*://www.ex.com/r.cfm?*"], "param": "u", "prependScheme": "https"],
        ["include": ["*://same.example.org/*"], "param": "to"],
    ],
    "stripParams": [
        ["params": ["(?:%3F)?utm(?:_[a-z_]*)?", "fbclid"]],
        ["urlPattern": "^https?://(?:[a-z0-9-]+\\.)*?shop\\.com", "params": ["ref_?"],
         "exceptions": ["^https?://shop\\.com/keep"]],
    ],
])

func nav(_ url: String, page: String? = "start.com", source: String = "start.com", main: Bool = true,
          kind: PageNavigationFacts.Kind = .link, tapped: Bool = false, redirect: Bool = false,
          fromApp: Bool = false, method: String = "GET", targetsMain: Bool = true) -> PageNavigationFacts {
    PageNavigationFacts(kind: kind, targetsMain: targetsMain, method: method, url: URL(string: url),
                        sourceIsMain: main, sourceHost: source, pageHost: page, redirect: redirect,
                        userInitiated: false, tappedRecently: tapped, fromApp: fromApp)
}

func loads(_ verdict: PageNavigationVerdict) -> String? {
    if case .load(let url) = verdict { return url.absoluteString }
    return nil
}

expect(loads(hopRules.judge(nav("https://go.example.net/out?url=https%3A%2F%2Fdest.org%2Fa%3Fb%3D1")))
       == "https://dest.org/a?b=1", "a hop by query parameter")
let encoded = Data("https://dest.org/x".utf8).base64EncodedString()
expect(loads(hopRules.judge(nav("https://t.b64.net/c?u=\(encoded)"))) == "https://dest.org/x", "a hop by base64")
expect(loads(hopRules.judge(nav("https://e.awstrack.me/L0/https:%2F%2Fdest.org%2Fp/1/abc")))
       == "https://dest.org/p", "a hop by path, percent-decoded")
expect(loads(hopRules.judge(nav("https://y2u.be/abc123"))) == "https://www.youtube.com/watch?v=abc123",
       "a hop by path template")
expect(loads(hopRules.judge(nav("https://www.ex.com/r.cfm?u=dest.org/z"))) == "https://dest.org/z",
       "a scheme put before a bare destination")
expect(hopRules.judge(nav("https://same.example.org/?to=https://www.example.org/")) == .allow,
       "a hop within one site is not taken")
expect(hopRules.judge(nav("https://go.example.net/out?url=https%3A%2F%2Fdest.org", method: "POST")) == .allow,
       "a post is left alone")
expect(loads(hopRules.judge(nav("https://news.org/a?id=4&utm_source=x&UTM_Medium=y&fbclid=z")))
       == "https://news.org/a?id=4", "tracking parameters taken out, ignoring case")
expect(loads(hopRules.judge(nav("https://news.org/a?utm_source=x"))) == "https://news.org/a",
       "an address left with no query loses its question mark")
expect(loads(hopRules.judge(nav("https://www.shop.com/p?ref=a&q=1"))) == "https://www.shop.com/p?q=1",
       "a site's own parameter")
expect(hopRules.judge(nav("https://shop.com/keep?ref=a")) == .allow, "an exception keeps the parameter")
expect(hopRules.judge(nav("https://other.com/p?ref=a")) == .allow, "a site's parameter stays elsewhere")
expect(hopRules.judge(nav("https://news.org/a?utm_source=x", kind: .backForward)) == .allow,
       "back and forward are left alone")

let jumps = rules(["blockUntappedJumps": true, "publicSuffixes": ["com", "co.uk"]])
let blocked = PageNavigationVerdict.block(reason: "script")
expect(jumps.judge(nav("https://ads.com/", kind: .other)) == blocked, "an untapped script jump is cancelled")
expect(jumps.judge(nav("https://ads.com/", kind: .link)) == blocked, "a script's click on a link is cancelled")
expect(jumps.judge(nav("https://ads.com/", tapped: true)) == .allow, "a tapped jump goes")
expect(jumps.judge(nav("https://www.start.com/b", kind: .other)) == .allow, "a jump within the site goes")
expect(jumps.judge(nav("https://ads.com/", kind: .form)) == .allow, "a form goes")
expect(jumps.judge(nav("https://ads.com/", redirect: true)) == .allow, "a server redirect goes")
expect(jumps.judge(nav("https://ads.com/", fromApp: true)) == .allow, "the app's own load goes")
expect(jumps.judge(nav("https://ads.com/", kind: .reload)) == .allow, "a reload goes")
expect(jumps.judge(nav("https://ads.com/", page: nil)) == .allow, "a new page's first load goes")
expect(jumps.judge(nav("https://ads.com/", targetsMain: false)) == .allow, "a frame's own navigation goes")
expect(jumps.judge(nav("https://www.start.com/", source: "frame.net", main: false))
       == .block(reason: "frame"), "a frame from another site cannot move the page, even within its site")
expect(jumps.judge(nav("https://ads.com/", source: "www.start.com", main: false)) == blocked,
       "a frame of the page cannot send it to another site")
expect(jumps.judge(nav("https://www.start.com/x", source: "cdn.start.com", main: false)) == .allow,
       "a frame of the page may move it within the site")
expect(jumps.judge(nav("https://ads.com/", source: "", main: false)) == .block(reason: "frame"),
       "a frame with no origin counts as another site")
expect(jumps.judge(nav("https://a.example.co.uk/", page: "b.example.co.uk", source: "b.example.co.uk")) == .allow,
       "sites follow the suffix list")
expect(rules([:]).judge(nav("https://ads.com/", kind: .other)) == .allow, "no rules, no blocking")

do {
    _ = try PageNavigationRules(["blockUntapedJumps": true])
    expect(false, "an unknown key is refused")
} catch {}
do {
    _ = try PageNavigationRules(["hops": [["include": ["*://a.com/*"]]]])
    expect(false, "a hop with no destination is refused")
} catch {}

func response(_ url: String = "https://files.example.org/a.bin", main: Bool = true, canShow: Bool = true,
              disposition: String? = nil) -> PageResponseFacts {
    PageResponseFacts(url: URL(string: url), mainFrame: main, canShow: canShow, contentDisposition: disposition)
}

let downloads = rules(["downloadsToApp": true])
expect(downloads.downloadReason(response(disposition: "attachment; filename=\"a.bin\"")) == "attachment",
       "an attachment is handed to the app")
expect(downloads.downloadReason(response(disposition: "  Attachment")) == "attachment",
       "the disposition is read ignoring case and leading space")
expect(downloads.downloadReason(response(canShow: false)) == "unshowable", "a type the page cannot show")
expect(downloads.downloadReason(response(canShow: false, disposition: "attachment")) == "attachment",
       "an attachment of an unshowable type is an attachment")
expect(downloads.downloadReason(response(disposition: "inline; filename=a.pdf")) == nil, "inline is shown")
expect(downloads.downloadReason(response(disposition: "inline; filename=attachment.pdf")) == nil,
       "the word only inside the header does not count")
expect(downloads.downloadReason(response()) == nil, "a page is shown")
expect(downloads.downloadReason(response(main: false, canShow: false, disposition: "attachment")) == nil,
       "a frame's file is left alone")
expect(downloads.downloadReason(response("blob:https://a.com/1", canShow: false)) == nil, "only http and https")
expect(downloads.downloadReason(response("data:text/plain,a", disposition: "attachment")) == nil, "not a data address")
expect(rules(["popupsNeedTap": true]).downloadReason(response(canShow: false)) == nil, "off unless the app sets it")
expect(rules(["downloadsToApp": false]).downloadReason(response(disposition: "attachment")) == nil, "set off")
expect(downloads.judge(nav("https://ads.com/", kind: .other)) == .allow, "downloads alone judge no jumps")

if failures > 0 {
    print("page navigation check: \(failures) failed")
    exit(1)
}
print("page navigation check: ok")
