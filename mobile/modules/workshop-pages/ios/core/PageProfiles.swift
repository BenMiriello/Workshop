import CryptoKit
import Foundation

// Each app's profile names <-> website data store identifiers.
//
// A profile's store identifier is a UUID v5 of its name under its app's own
// namespace (a UUID v5 of the app id), so the same app and name always open
// the same store and two apps never share one. The app -> name -> UUID map is
// also kept in the defaults, so a listing names the stores it finds and an
// app is shown only its own.
//
// Stores from before profiles were kept per app were named alone, under
// `legacyKey`. The first app to reach its profiles afterwards takes that map
// over whole, identifiers unchanged, so its stores keep their cookies; it
// also owns any store no app has a name for. Those stores had one user.
enum PageProfiles {
    static var defaults = UserDefaults.standard
    private static let mapKey = "workshop.pages.appProfiles"
    private static let legacyKey = "workshop.pages.profiles"
    private static let ownerKey = "workshop.pages.profilesOwner"
    private static let legacyNamespace: [UInt8] = [
        0xaa, 0xfa, 0x74, 0x22, 0x51, 0x87, 0x4a, 0xde,
        0xb5, 0x2b, 0x48, 0xad, 0x6d, 0x2b, 0xb3, 0xb0,
    ]
    private static let appsNamespace: [UInt8] = [
        0x5c, 0xb8, 0x63, 0x26, 0xe8, 0x7b, 0x9a, 0x49,
        0x3e, 0x54, 0xea, 0x9f, 0x0e, 0xef, 0x84, 0xc8,
    ]

    static func uuid5(_ namespace: [UInt8], _ name: String) -> UUID {
        var b = Array(Insecure.SHA1.hash(data: Data(namespace + Array(name.utf8))))
        b[6] = (b[6] & 0x0f) | 0x50
        b[8] = (b[8] & 0x3f) | 0x80
        return UUID(uuid: (
            b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
            b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]
        ))
    }

    /// The identifier a store named `name` was given before profiles were per app.
    static func legacyIdentifier(for name: String) -> UUID {
        uuid5(legacyNamespace, name)
    }

    static func derivedIdentifier(app: String, name: String) -> UUID {
        let space = withUnsafeBytes(of: uuid5(appsNamespace, app).uuid) { Array($0) }
        return uuid5(space, name)
    }

    private static func map() -> [String: [String: String]] {
        (defaults.dictionary(forKey: mapKey) as? [String: [String: String]]) ?? [:]
    }

    private static func save(_ map: [String: [String: String]]) {
        defaults.set(map, forKey: mapKey)
    }

    /// Gives `app` the stores named before profiles were per app, when no app has them yet.
    private static func claimLegacy(by app: String) {
        guard let legacy = defaults.dictionary(forKey: legacyKey) as? [String: String] else { return }
        var all = map()
        all[app] = legacy.merging(all[app] ?? [:]) { _, own in own }
        save(all)
        if defaults.string(forKey: ownerKey) == nil {
            defaults.set(app, forKey: ownerKey)
        }
        defaults.removeObject(forKey: legacyKey)
    }

    static func names(app: String) -> [String: String] {
        claimLegacy(by: app)
        return map()[app] ?? [:]
    }

    /// The store identifier for `app`'s profile `name`, recorded in the map.
    static func identifier(app: String, name: String) -> UUID {
        if let known = names(app: app)[name], let id = UUID(uuidString: known) {
            return id
        }
        let id = derivedIdentifier(app: app, name: name)
        var all = map()
        all[app, default: [:]][name] = id.uuidString
        save(all)
        return id
    }

    /// Existing stores no app has a name for, which only the owner of the
    /// stores from before profiles were per app is shown.
    private static func unnamed(_ existing: [UUID], for app: String) -> [UUID] {
        guard defaults.string(forKey: ownerKey) == app else { return [] }
        let named = Set(map().values.flatMap { $0.values })
        return existing.filter { !named.contains($0.uuidString) }
    }

    /// The store `app`'s `profile` refers to: one of its names, else the UUID
    /// string of an unnamed store it owns (how those are listed); nil for none.
    static func resolve(app: String, _ profile: String, existing: [UUID]) -> UUID? {
        if let known = names(app: app)[profile], let id = UUID(uuidString: known) {
            return id
        }
        guard let id = UUID(uuidString: profile) else { return nil }
        return unnamed(existing, for: app).contains(id) ? id : nil
    }

    static func forget(app: String, _ id: UUID) {
        var all = map()
        all[app] = all[app]?.filter { $0.value != id.uuidString }
        save(all)
    }

    /// `app`'s names for the stores that exist, and the UUIDs of unnamed ones it owns.
    static func list(app: String, existing: [UUID]) -> [String] {
        let byId = Dictionary(names(app: app).map { ($0.value, $0.key) }, uniquingKeysWith: { a, _ in a })
        let own = existing.compactMap { byId[$0.uuidString] }
        return (own + unnamed(existing, for: app).map(\.uuidString)).sorted()
    }
}
