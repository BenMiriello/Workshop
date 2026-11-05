import Foundation

/// Cookies to and from the bridge's plain objects (`PageCookie` in
/// shared/native-pages.ts).
enum PageCookies {
    static func export(_ c: HTTPCookie) -> [String: Any] {
        var sameSite = "None"
        if c.sameSitePolicy == .sameSiteLax {
            sameSite = "Lax"
        } else if c.sameSitePolicy == .sameSiteStrict {
            sameSite = "Strict"
        }
        return [
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "expires": c.expiresDate?.timeIntervalSince1970 ?? -1.0,
            "secure": c.isSecure,
            "httpOnly": c.isHTTPOnly,
            "sameSite": sameSite,
        ]
    }

    static func cookie(from d: [String: Any]) -> HTTPCookie? {
        guard let name = d["name"] as? String,
              let value = d["value"] as? String,
              let domain = d["domain"] as? String, !name.isEmpty, !domain.isEmpty
        else { return nil }
        var props: [HTTPCookiePropertyKey: Any] = [
            .name: name,
            .value: value,
            .domain: domain,
            .path: (d["path"] as? String) ?? "/",
        ]
        if let expires = (d["expires"] as? NSNumber)?.doubleValue, expires > 0 {
            props[.expires] = Date(timeIntervalSince1970: expires)
        }
        if (d["secure"] as? Bool) == true {
            props[.secure] = "TRUE"
        }
        if (d["httpOnly"] as? Bool) == true {
            props[HTTPCookiePropertyKey("HttpOnly")] = "TRUE"
        }
        switch (d["sameSite"] as? String)?.lowercased() {
        case "lax": props[.sameSitePolicy] = HTTPCookieStringPolicy.sameSiteLax.rawValue
        case "strict": props[.sameSitePolicy] = HTTPCookieStringPolicy.sameSiteStrict.rawValue
        default: break
        }
        return HTTPCookie(properties: props)
    }
}
