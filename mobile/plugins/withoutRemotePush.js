// Config plugin: strip the `aps-environment` entitlement.
//
// expo-notifications' config plugin injects `aps-environment` (the remote-push
// capability) into the iOS entitlements. Workshop only uses LOCAL notifications
// (remote push is a deferred, paid-tier seam), and a free Apple Personal Team
// cannot sign an app that requests Push Notifications — the build fails with
// "Personal development teams do not support the Push Notifications capability".
//
// Local notifications work without this entitlement, so we remove it here. This
// plugin must run AFTER "expo-notifications" in app.json's plugins array so it
// deletes the key the notifications plugin added. When remote push is enabled
// (paid tier), drop this plugin.

const { withEntitlementsPlist } = require("@expo/config-plugins");

module.exports = function withoutRemotePush(config) {
  return withEntitlementsPlist(config, (cfg) => {
    delete cfg.modResults["aps-environment"];
    return cfg;
  });
};
