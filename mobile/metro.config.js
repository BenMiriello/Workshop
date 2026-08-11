// Metro config for the Workshop mobile app.
//
// The app lives in mobile/ inside the Workshop monorepo and imports shared code
// from the repo-root shared/ folder via the "@shared/*" alias (see tsconfig
// paths). Metro does NOT read tsconfig paths and does NOT watch folders outside
// the project root, so `import ... from "@shared/native-bridge"` fails to bundle
// (type-only imports are stripped by Babel, but native/inject.ts imports runtime
// values, so the alias must resolve for the Release bundle). Wire both up here:
//   - watchFolders: include ../shared so Metro can read/bundle it
//   - resolver.extraNodeModules: map the "@shared" prefix to ../shared

const { getDefaultConfig } = require("expo/metro-config");
const path = require("path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "..");
const sharedRoot = path.resolve(workspaceRoot, "shared");
const kitRoot = path.resolve(workspaceRoot, "packages/ui");

const config = getDefaultConfig(projectRoot);

config.watchFolders = [...(config.watchFolders ?? []), sharedRoot, kitRoot];

config.resolver.extraNodeModules = {
  ...(config.resolver.extraNodeModules ?? {}),
  "@shared": sharedRoot,
};

// @workshop/ui — the design system kit (packages/ui). Metro does not read a
// package's "exports" map by default, so an extraNodeModules directory mapping
// would resolve "@workshop/ui/tokens.js" to <kit>/tokens.js, which is not where
// that file lives, and would also make every internal module reachable.
//
// This app may ONLY use the token layer: the React primitives are react-dom and
// cannot render here. So the one published subpath the app is allowed to import
// is mapped explicitly, and everything else under @workshop/ui is refused with a
// message that says why. That mirrors the exports map rather than working
// around it.
const KIT_SUBPATHS = {
  "@workshop/ui/tokens.js": path.resolve(kitRoot, "src/styles/tokens.js"),
};

const upstreamResolve = config.resolver.resolveRequest;

config.resolver.resolveRequest = (context, moduleName, platform) => {
  const mapped = KIT_SUBPATHS[moduleName];
  if (mapped) {
    return { type: "sourceFile", filePath: mapped };
  }
  if (moduleName === "@workshop/ui" || moduleName.startsWith("@workshop/ui/")) {
    throw new Error(
      `${moduleName} is not available in the mobile app. React Native cannot render ` +
        "the react-dom primitives; import '@workshop/ui/tokens.js' and build the " +
        "component with StyleSheet. See packages/ui/README.md."
    );
  }
  return upstreamResolve
    ? upstreamResolve(context, moduleName, platform)
    : context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
