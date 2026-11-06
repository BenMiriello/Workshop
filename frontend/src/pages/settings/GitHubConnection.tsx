import { ExternalLink, Link2Off, MoreVertical, RefreshCw, Link2 } from "lucide-react";
import { Button, Menu, MenuCheckItem, MenuItem, MenuSeparator, Notice, Row, Spacer, Stack } from "@workshop/ui";
import type { components } from "../../api/schema.gen";

// The connected GitHub account: who, what it reaches, and one action for the
// state it is in. Everything secondary is in the menu.

type Status = components["schemas"]["StatusOut"];
type Installation = components["schemas"]["InstallationOut"];

function open(url: string) {
  window.open(url, "_blank", "noopener");
}

function reach(i: Installation): string {
  switch (i.state) {
    case "installed":
      if (i.repository_selection !== "selected") return "All repositories";
      if (i.repository_count == null) return "Selected repositories";
      return i.repository_count === 1 ? "1 repository" : `${i.repository_count} repositories`;
    case "missing": return "Not installed on your account";
    case "signed_out": return "Signed out on GitHub";
    default: return "Couldn't reach GitHub";
  }
}

export function GitHubConnection({ data, busy, onReconnect, onRetry, onHelper, onDisconnect }: {
  data: Status;
  busy: string | null;
  onReconnect: () => void;
  onRetry: () => void;
  onHelper: (on: boolean) => void;
  onDisconnect: () => void;
}) {
  const inst = data.installation;

  const action = !inst ? null
    : inst.state === "installed" ? (
      inst.manage_url && (
        <Button size="sm" variant="ghost" onClick={() => open(inst.manage_url)}>
          <ExternalLink aria-hidden /> Manage on GitHub
        </Button>
      ))
    : inst.state === "missing" ? (
      <Button size="sm" variant="primary" onClick={() => open(data.install_url)}>
        <ExternalLink aria-hidden /> Install on GitHub
      </Button>)
    : inst.state === "signed_out" ? (
      <Button size="sm" variant="primary" onClick={onReconnect}>
        <Link2 aria-hidden /> Reconnect
      </Button>)
    : (
      <Button size="sm" loading={busy === "check"} onClick={onRetry}>
        <RefreshCw aria-hidden /> Retry
      </Button>);

  return (
    <Stack gap="sm">
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
        <img src={`https://github.com/${encodeURIComponent(data.login)}.png?size=64`} alt=""
             width={32} height={32} style={{ borderRadius: "var(--radius-pill)" }} />
        <Stack>
          <strong>{data.login}</strong>
          {inst && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{reach(inst)}</span>}
        </Stack>
        <Spacer />
        {action}
        <Menu label="GitHub account" trigger={
          <Button size="sm" variant="ghost" icon aria-label="More">
            <MoreVertical aria-hidden />
          </Button>
        }>
          <MenuCheckItem checked={data.helper_installed} disabled={busy === "helper"}
                         onToggle={() => onHelper(!data.helper_installed)}>
            Use for git on this computer
          </MenuCheckItem>
          <MenuSeparator />
          <MenuItem danger icon={<Link2Off size={14} aria-hidden />} onSelect={onDisconnect}>
            Disconnect
          </MenuItem>
        </Menu>
      </Row>

      {inst?.blocked.map((account) => (
        <Notice key={account} tone="error" action={
          <Button size="sm" variant="ghost"
                  onClick={() => open(`https://github.com/organizations/${account}/settings/installations`)}>
            <ExternalLink aria-hidden /> Open {account}
          </Button>
        }>
          Also installed on {account}, which this installation must not reach.
        </Notice>
      ))}
    </Stack>
  );
}
