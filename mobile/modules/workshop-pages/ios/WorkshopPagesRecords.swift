import ExpoModulesCore
import Foundation

// The argument shapes of the module's functions, as the JS side sends them
// (shared/native-pages.ts).

struct PageRect: Record {
    @Field var x: Double = 0
    @Field var y: Double = 0
    @Field var width: Double = 0
    @Field var height: Double = 0
}

struct PageProxy: Record {
    @Field var host: String = ""
    @Field var port: Int = 0
    @Field var kind: String = "socks5"
}

extension PageRect {
    var frame: PageFrame { PageFrame(x: x, y: y, width: width, height: height) }
}

struct PageOpenOptions: Record {
    @Field var profile: String = ""
    @Field var url: String = ""
    @Field var rect: PageRect = PageRect()
    @Field var proxy: PageProxy? = nil
    @Field var trustCertDER: [String]? = nil
    @Field var userAgent: String? = nil
    @Field var contentMode: String? = nil

    var request: PageOpenRequest {
        PageOpenRequest(
            profile: profile,
            url: url,
            rect: rect.frame,
            proxy: proxy.map { PageProxySetting(host: $0.host, port: $0.port, kind: $0.kind) },
            trustCertDER: trustCertDER ?? [],
            userAgent: userAgent,
            contentMode: contentMode
        )
    }
}

struct PageUserScriptOptions: Record {
    @Field var source: String = ""
    @Field var world: String? = nil
    @Field var frames: String? = nil
    @Field var at: String? = nil

    var script: PageUserScript {
        .with(source: source, world: world, frames: frames, at: at)
    }
}

struct PageRunOptions: Record {
    @Field var world: String? = nil
}
