// Config plugin: the Workshop keyboard (targets/WorkshopKeyboard), a custom
// keyboard extension listing the clipboard's recent clips. Prebuild
// regenerates ios/ on every SideStore build, so the target is added here each
// time rather than once in Xcode.
//
// The app and the keyboard share one app group, group.<bundle id>: the app
// leaves the API base and its sign-in there (ClipboardCapture.share). Both
// entitlements files hold literal ids, because the SideStore build signs with
// the files themselves and SideStore registers what the signature names.

const fs = require("fs");
const path = require("path");
const {
  withEntitlementsPlist,
  withInfoPlist,
  withXcodeProject,
} = require("@expo/config-plugins");

const TARGET = "WorkshopKeyboard";
const SOURCES = path.join(__dirname, "..", "targets", TARGET);
const SHARED = path.join(__dirname, "..", "modules", "workshop-clipboard", "ios", "WorkshopGroup.swift");
const GROUPS = "com.apple.security.application-groups";
const GROUP_KEY = "WorkshopAppGroup";

const groupOf = (config) => `group.${config.ios.bundleIdentifier}`;

function entitlementsXml(group) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>${GROUPS}</key>
	<array>
		<string>${group}</string>
	</array>
</dict>
</plist>
`;
}

function copySources(dir, group) {
  fs.mkdirSync(dir, { recursive: true });
  for (const name of fs.readdirSync(SOURCES)) {
    fs.copyFileSync(path.join(SOURCES, name), path.join(dir, name));
  }
  fs.copyFileSync(SHARED, path.join(dir, path.basename(SHARED)));
  fs.writeFileSync(path.join(dir, `${TARGET}.entitlements`), entitlementsXml(group));
  return fs.readdirSync(dir);
}

function addTarget(project, files, settings) {
  const app = project.getFirstTarget();
  const target = project.addTarget(TARGET, "app_extension", TARGET, settings.PRODUCT_BUNDLE_IDENTIFIER);
  const group = project.addPbxGroup(files, TARGET, TARGET);
  project.addToPbxGroup(group.uuid, project.getFirstProject().firstProject.mainGroup);
  const swift = files.filter((f) => f.endsWith(".swift"));
  project.addBuildPhase(swift, "PBXSourcesBuildPhase", "Sources", target.uuid);
  project.addBuildPhase([], "PBXResourcesBuildPhase", "Resources", target.uuid);
  project.addBuildPhase([], "PBXFrameworksBuildPhase", "Frameworks", target.uuid);
  project.addTargetDependency(app.uuid, [target.uuid]);

  const configs = project.pbxXCBuildConfigurationSection();
  for (const key of Object.keys(configs)) {
    const cfg = configs[key];
    if (!cfg.buildSettings || cfg.buildSettings.PRODUCT_NAME !== `"${TARGET}"`) continue;
    Object.assign(cfg.buildSettings, settings);
  }
}

module.exports = function withKeyboardExtension(config) {
  const group = groupOf(config);

  config = withEntitlementsPlist(config, (cfg) => {
    cfg.modResults[GROUPS] = [group];
    // The build is signed with this file now, and a free Apple account
    // cannot sign push (app.json extra.comment:notifications).
    if ((cfg.extra && cfg.extra.tier) !== "paid") delete cfg.modResults["aps-environment"];
    return cfg;
  });

  config = withInfoPlist(config, (cfg) => {
    cfg.modResults[GROUP_KEY] = group;
    return cfg;
  });

  return withXcodeProject(config, (cfg) => {
    const project = cfg.modResults;
    const files = copySources(path.join(cfg.modRequest.platformProjectRoot, TARGET), group);
    const targets = Object.values(project.pbxNativeTargetSection());
    if (targets.some((t) => t && [TARGET, `"${TARGET}"`].includes(t.name))) return cfg;
    addTarget(project, files, {
      PRODUCT_NAME: `"${TARGET}"`,
      PRODUCT_BUNDLE_IDENTIFIER: `"${cfg.ios.bundleIdentifier}.keyboard"`,
      INFOPLIST_FILE: `"${TARGET}/Info.plist"`,
      CODE_SIGN_ENTITLEMENTS: `"${TARGET}/${TARGET}.entitlements"`,
      WORKSHOP_APP_GROUP: `"${group}"`,
      WORKSHOP_DISPLAY_NAME: `"${cfg.name}"`,
      MARKETING_VERSION: `"${cfg.version || "1.0"}"`,
      CURRENT_PROJECT_VERSION: "1",
      SWIFT_VERSION: "5.0",
      // The keyboard's own floor (async URLSession), not the project's 13.4.
      IPHONEOS_DEPLOYMENT_TARGET: "15.0",
      TARGETED_DEVICE_FAMILY: `"1,2"`,
      APPLICATION_EXTENSION_API_ONLY: "YES",
      CLANG_ENABLE_MODULES: "YES",
      GENERATE_INFOPLIST_FILE: "NO",
      SKIP_INSTALL: "YES",
    });
    return cfg;
  });
};
