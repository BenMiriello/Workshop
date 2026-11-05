import Foundation
import Network
import WebKit

/// A data store's proxy, and the certificates a page trusts beside the system's.
extension PagePool {
    @available(iOS 17.0, *)
    func applyProxy(_ proxy: PageProxySetting, to store: WKWebsiteDataStore) throws {
        guard !proxy.host.isEmpty, proxy.port > 0, proxy.port <= 65535,
              let port = NWEndpoint.Port(rawValue: UInt16(proxy.port))
        else { throw PageError.fail("Proxy needs a host and a port from 1 to 65535") }
        let endpoint = NWEndpoint.hostPort(host: NWEndpoint.Host(proxy.host), port: port)
        switch proxy.kind {
        case "socks5":
            store.proxyConfigurations = [ProxyConfiguration(socksv5Proxy: endpoint)]
        case "http":
            store.proxyConfigurations = [ProxyConfiguration(httpCONNECTProxy: endpoint, tlsOptions: nil)]
        default:
            throw PageError.fail("Proxy kind must be socks5 or http")
        }
    }

    func certificates(_ list: [String]) throws -> [SecCertificate] {
        try list.map { encoded in
            guard let data = Data(base64Encoded: encoded, options: .ignoreUnknownCharacters),
                  let cert = SecCertificateCreateWithData(nil, data as CFData)
            else { throw PageError.fail("A trust certificate is not base64 DER") }
            return cert
        }
    }
}
