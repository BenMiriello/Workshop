// The whole contract: one default export, components keyed by the names the
// manifest referenced, and no props on any of them.
// The app's OWN stylesheet, imported from its entry so it is part of this
// bundle's graph and the build emits exactly one file for it.
import "./app.css";
import { useState } from "react";
import { Button, Stack } from "@workshop/ui";
import { useAppHost } from "@workshop/app-host";

function Home() {
  // A HOOK ON PURPOSE. If this app resolved a second copy of React, this line
  // is where it throws, which is what the import map and the shared-runtime
  // guard exist to prevent.
  const [clicks, setClicks] = useState(0);
  // The shell's own services, reached through the seam rather than through a
  // prop the platform's registry would have to pass.
  const { appId } = useAppHost();
  return (
    <Stack gap="md">
      <div className="wsfix-probe" data-app={appId}>
        Loaded at runtime, clicked {clicks} times.
      </div>
      <Button variant="primary" onClick={() => setClicks((n) => n + 1)}>
        Click
      </Button>
    </Stack>
  );
}

export default { contract: 1, exports: { Home } };
