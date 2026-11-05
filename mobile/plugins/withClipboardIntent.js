// Config plugin: compiles the "Save Clipboard to Workshop" App Intent into the
// app target. Shortcuts reads App Intents from the app's own sources only, so
// the intent cannot live in the clipboard module's pod with the rest of it.
// Prebuild regenerates ios/, so the file is copied in on every run.

const fs = require("fs");
const path = require("path");
const { IOSConfig, withXcodeProject } = require("@expo/config-plugins");

const FILE = "SaveClipboardIntent.swift";
const SOURCE = path.join(__dirname, "..", "modules", "workshop-clipboard", "intent", FILE);

module.exports = function withClipboardIntent(config) {
  return withXcodeProject(config, (cfg) => {
    const { platformProjectRoot, projectName } = cfg.modRequest;
    fs.copyFileSync(SOURCE, path.join(platformProjectRoot, projectName, FILE));
    const filepath = `${projectName}/${FILE}`;
    if (!cfg.modResults.hasFile(filepath)) {
      IOSConfig.XcodeUtils.addBuildSourceFileToGroup({
        filepath,
        groupName: projectName,
        project: cfg.modResults,
      });
    }
    return cfg;
  });
};
