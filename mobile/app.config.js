// Dynamic Expo config: layer optional env overrides on top of app.json.
//
// app.json holds the defaults; these env vars let a build carry its own app
// identity and target a different backend origin WITHOUT editing committed
// config (mirrors the EXPO_PUBLIC_WORKSHOP_URL override that native/config.ts
// already reads for the backend URL). Unset => the app.json defaults are used
// unchanged, so a build that sets nothing is byte-identical to app.json.
//
// This is what lets a SECOND Workshop instance's app coexist on one device:
// a distinct bundle id + name via env, same source. Per-instance build values
// go in mobile/.env.local (gitignored), never in committed config.
//
//   WORKSHOP_APP_NAME        display name
//   WORKSHOP_APP_SLUG        expo slug
//   WORKSHOP_APP_SCHEME      deep-link scheme
//   WORKSHOP_IOS_BUNDLE_ID   iOS bundle identifier
//
module.exports = ({ config }) => {
  const name = process.env.WORKSHOP_APP_NAME || config.name;
  const slug = process.env.WORKSHOP_APP_SLUG || config.slug;
  const scheme = process.env.WORKSHOP_APP_SCHEME || config.scheme;
  const bundleIdentifier =
    process.env.WORKSHOP_IOS_BUNDLE_ID || (config.ios && config.ios.bundleIdentifier);

  return {
    ...config,
    name,
    slug,
    scheme,
    ios: { ...config.ios, bundleIdentifier },
  };
};
