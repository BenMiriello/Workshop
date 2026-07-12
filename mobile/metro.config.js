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

const config = getDefaultConfig(projectRoot);

config.watchFolders = [...(config.watchFolders ?? []), sharedRoot];

config.resolver.extraNodeModules = {
  ...(config.resolver.extraNodeModules ?? {}),
  "@shared": sharedRoot,
};

module.exports = config;
