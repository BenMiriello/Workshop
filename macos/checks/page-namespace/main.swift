import Foundation

// Run on the Mac by scripts/lib/macos-build-mac.sh before the app is built,
// compiled with the page views' PageApps, PageError and PageProfiles: each
// app's profile stores are its own, and the stores from before profiles were
// per app keep their identifiers.

var failures = 0

func expect(_ ok: Bool, _ what: String) {
    if !ok {
        failures += 1
        print("FAIL: \(what)")
    }
}

let suite = "workshop.pages.check.\(UUID().uuidString)"
guard let defaults = UserDefaults(suiteName: suite) else { fatalError("No defaults suite \(suite)") }
PageProfiles.defaults = defaults

// The value the shell gave a store named "default" before profiles were per app.
let old = PageProfiles.legacyIdentifier(for: "default")
expect(old.uuidString == "44820286-7FE9-5539-8F4D-72FBF2C40AAE", "the derivation from before is unchanged")

let a = "app-a", b = "app-b", c = "app-c"
let names = (0..<500).map { "p\($0)" } + ["default", "", "x/y", "app-b"]
let idsA = Set(names.map { PageProfiles.derivedIdentifier(app: a, name: $0) })
let idsB = Set(names.map { PageProfiles.derivedIdentifier(app: b, name: $0) })
let legacy = Set(names.map(PageProfiles.legacyIdentifier))
expect(idsA.count == names.count && idsB.count == names.count, "one store per name")
expect(idsA.isDisjoint(with: idsB), "two apps' stores are disjoint")
expect(idsA.isDisjoint(with: legacy) && idsB.isDisjoint(with: legacy), "new stores never land on old ones")

// A device from before: the map as the old code wrote it, plus a store it never named.
let work = PageProfiles.legacyIdentifier(for: "work")
let unnamed = UUID()
defaults.set(["default": old.uuidString, "work": work.uuidString], forKey: "workshop.pages.profiles")

expect(PageProfiles.identifier(app: a, name: "default") == old, "the first app keeps the old store and its cookies")
expect(PageProfiles.identifier(app: a, name: "work") == work, "every old name moves with it")
let bDefault = PageProfiles.identifier(app: b, name: "default")
expect(bDefault != old, "another app's profile of the same name is another store")
expect(PageProfiles.identifier(app: c, name: "work") == PageProfiles.derivedIdentifier(app: c, name: "work"),
       "the old stores are taken over once")

let existing = [old, work, bDefault, unnamed]
expect(PageProfiles.list(app: a, existing: existing) == ["default", "work", unnamed.uuidString].sorted(),
       "the first app lists its stores and the unnamed one")
expect(PageProfiles.list(app: b, existing: existing) == ["default"], "another app lists only its own")
expect(PageProfiles.resolve(app: b, "work", existing: existing) == nil, "another app cannot reach a profile by name")
expect(PageProfiles.resolve(app: b, old.uuidString, existing: existing) == nil, "nor by its store's UUID")
expect(PageProfiles.resolve(app: b, unnamed.uuidString, existing: existing) == nil, "nor an unnamed store")
expect(PageProfiles.resolve(app: a, unnamed.uuidString, existing: existing) == unnamed, "the owner reaches it")

let split = PageApps.split(PageApps.key(a, "x/y"))
expect(split?.app == a && split?.name == "x/y", "a key splits back into app and name")
expect(PageApps.valid(a) && !PageApps.valid("") && !PageApps.valid("a/b") && !PageApps.valid("../a"), "app ids")

defaults.removePersistentDomain(forName: suite)
if failures > 0 {
    print("page namespace check: \(failures) failed")
    exit(1)
}
print("page namespace check: ok")
