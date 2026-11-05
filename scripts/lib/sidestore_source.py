"""Write a SideStore (AltStore-format) source.json for the built ipa.

Usage: sidestore_source.py <dir> <app name> <base url> [file name]
<dir> holds workshop.ipa plus the .Info.plist and .entitlements.plist copied
from the build; the source is written as [file name] (default source.json),
so one build can be listed at more than one address. The permissions are read from the app itself, because
SideStore refuses an install whose listed permissions differ from the ipa's.
"""
import datetime
import json
import os
import plistlib
import sys

out, name, base = sys.argv[1], sys.argv[2], sys.argv[3].rstrip("/")
file_name = sys.argv[4] if len(sys.argv) > 4 else "source.json"
with open(os.path.join(out, ".Info.plist"), "rb") as f:
    info = plistlib.load(f)
with open(os.path.join(out, ".entitlements.plist"), "rb") as f:
    entitlements = plistlib.load(f)

privacy = {k: v for k, v in info.items()
           if k.startswith("NS") and k.endswith("UsageDescription")}
ipa = os.path.join(out, "workshop.ipa")
icon = os.path.join(out, "icon.png")
if not os.path.exists(icon):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (512, 512), (24, 26, 32))
    ImageDraw.Draw(img).text((256, 256), name[:1], fill=(230, 232, 236),
                             anchor="mm", font_size=300)
    img.save(icon)
version = {
    "version": info.get("CFBundleShortVersionString", "0.0.1"),
    "buildVersion": info["CFBundleVersion"],
    "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "downloadURL": f"{base}/workshop.ipa?b={info['CFBundleVersion']}",
    "size": os.path.getsize(ipa),
    "minOSVersion": info.get("MinimumOSVersion", "16.2"),
}
source = {
    "name": name,
    "identifier": f"{info['CFBundleIdentifier']}.source",
    "apps": [{
        "name": info.get("CFBundleDisplayName") or name,
        "bundleIdentifier": info["CFBundleIdentifier"],
        "developerName": name,
        "localizedDescription": f"{name} on this phone.",
        "iconURL": f"{base}/icon.png",
        "versions": [version],
        "appPermissions": {"entitlements": sorted(entitlements), "privacy": privacy},
    }],
    "news": [],
}
with open(os.path.join(out, file_name), "w") as f:
    json.dump(source, f, indent=2)
print(f"{file_name}: {version['version']} ({version['buildVersion']}), "
      f"{len(privacy)} privacy keys, {len(entitlements)} entitlements")
