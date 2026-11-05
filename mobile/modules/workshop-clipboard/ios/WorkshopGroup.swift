import Foundation

/// The app group the Workshop app shares with its keyboard: the app leaves
/// the origin's API base and its sign-in there, because the keyboard runs in
/// its own sandbox and cannot read the web view's cookies or the app's
/// keychain. Compiled into both: the clipboard module and, copied in by
/// plugins/withKeyboardExtension.js, the keyboard target.
///
/// SideStore registers the group under the signing team and renames it (the
/// team id appended), so the id in use is read from the provisioning profile
/// the bundle was signed with. With no profile, as in the simulator, it is the
/// declared one the build wrote into the Info.plist.
enum WorkshopGroup {
    static let apiBaseKey = "apiBase"
    static let tokenKey = "deviceToken"
    static let cookieKey = "sessionCookie"
    private static let declaredKey = "WorkshopAppGroup"
    private static let groupsEntitlement = "com.apple.security.application-groups"

    static let id: String? = signedGroup() ?? declared

    static var defaults: UserDefaults? { id.flatMap { UserDefaults(suiteName: $0) } }

    private static var declared: String? {
        Bundle.main.object(forInfoDictionaryKey: declaredKey) as? String
    }

    /// The group in the bundle's embedded.mobileprovision: a signed message
    /// whose content is a plain XML property list.
    private static func signedGroup() -> String? {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let profile = try? PropertyListSerialization.propertyList(
                  from: data.subdata(in: start.lowerBound..<end.upperBound), format: nil) as? [String: Any],
              let entitlements = profile["Entitlements"] as? [String: Any],
              let groups = entitlements[groupsEntitlement] as? [String]
        else { return nil }
        if let declared, let match = groups.first(where: { $0.hasPrefix(declared) }) { return match }
        return groups.first
    }
}
