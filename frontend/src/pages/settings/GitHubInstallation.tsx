import { ExternalLink, RefreshCw } from "lucide-react";
import { Button, Notice, Row, Stack } from "@workshop/ui";
import type { components } from "../../api/schema.gen";

// The second half of connecting GitHub. A GitHub App's sign-in reaches no
// repository by itself: it reaches only the accounts the app is installed on,
// which is also what keeps an organization the operator belongs to out of reach
// (backend/services/github_identity.py, installation_state).
//
// The approval happens on github.com, and the card re-reads the connection when
// the page comes back into view, so "Check again" is only for a return the
// browser did not announce.

type InstallationOut = components["schemas"]["InstallationOut"];

const REACH: Record<string, string> = {
  all: "Reaches all your repositories.",
  selected: "Reaches the repositories you picked.",
};

function open(url: string) {
  window.open(url, "_blank", "noopener");
}

export function Installation({ installation, installUrl, checking, onCheck }: {
  installation: InstallationOut;
  installUrl: string;
  checking: boolean;
  onCheck: () => void;
}) {
  const check = (
    <Button size="sm" variant="ghost" loading={checking} disabled={checking} onClick={onCheck}>
      <RefreshCw aria-hidden /> Check again
    </Button>
  );

  return (
    <Stack gap="sm">
      {installation.state === "installed" && (
        <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            {REACH[installation.repository_selection] ?? "Installed on your account."}
          </span>
          <Button size="sm" variant="ghost" onClick={() => open(installUrl)}>
            <ExternalLink aria-hidden /> Change on GitHub
          </Button>
        </Row>
      )}

      {installation.state === "missing" && (
        <Notice tone="warn" action={check}>
          Install the GitHub app on your account so Workshop can reach your repositories.
        </Notice>
      )}
      {installation.state === "missing" && (
        <Row>
          <Button size="sm" variant="primary" onClick={() => open(installUrl)}>
            <ExternalLink aria-hidden /> Install on GitHub
          </Button>
        </Row>
      )}

      {installation.state === "unknown" && (
        <Notice tone="info" action={check}>
          Couldn't check where the GitHub app is installed.
        </Notice>
      )}

      {installation.blocked.map((account) => (
        <Notice key={account} tone="error" action={
          <Button size="sm" variant="ghost"
                  onClick={() => open(`https://github.com/organizations/${account}/settings/installations`)}>
            <ExternalLink aria-hidden /> Open {account}
          </Button>
        }>
          The GitHub app is also installed on {account}, which this installation must not reach.
          Uninstall it there.
        </Notice>
      ))}
    </Stack>
  );
}
