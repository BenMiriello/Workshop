// Config plugin: guarantees the app target declares Live Activities support.
//
// Creating the Widget Extension TARGET itself from a config plugin is fragile
// (it rewrites the Xcode project), so that remains a one-time manual step in
// Xcode — see mobile/README.md "Live Activity setup". This plugin handles the
// reliably-automatable half so prebuild never drops the Info.plist key.

const { withInfoPlist } = require("@expo/config-plugins");

module.exports = function withLiveActivity(config) {
  return withInfoPlist(config, (cfg) => {
    cfg.modResults.NSSupportsLiveActivities = true;
    return cfg;
  });
};
