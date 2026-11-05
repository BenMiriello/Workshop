// Where a web link in a chat opens (lib/webLinks.ts, services/web_links.py):
// a new window, or an installed app that declares it opens web pages.

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Globe } from "lucide-react";
import { Notice, Select, SettingsRow as Row, SettingsSection as Section, Skeleton } from "@workshop/ui";
import { chooseWebLinks, useWebLinks } from "../../lib/webLinks";
import { queryKeys } from "../../lib/queryKeys";

const WINDOW = "";

export function WebLinksSetting() {
  const q = useWebLinks();
  const qc = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const choose = async (app: string) => {
    setErr(null);
    setSaving(true);
    try {
      qc.setQueryData(queryKeys.webLinks(), await chooseWebLinks(app || null));
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section icon={Globe}>
      <Row
        label="web links"
        hint="Where a link to a web page in a chat opens. Links to Workshop pages always open here."
      >
        {q.isPending ? (
          <Skeleton width="12rem" style={{ height: "var(--control-h)" }} />
        ) : q.isError ? (
          <Notice tone="error" where="GET /api/web-links">Couldn't load where web links open.</Notice>
        ) : (
          <Select
            value={q.data.chosen?.app ?? WINDOW}
            onChange={(e) => { void choose(e.target.value); }}
            disabled={saving}
            aria-label="Open web links in"
          >
            <option value={WINDOW}>A new window</option>
            {q.data.handlers.map((h) => <option key={h.app} value={h.app}>{h.label}</option>)}
          </Select>
        )}
      </Row>
      {err && <Notice tone="error">{`Couldn't save: ${err}`}</Notice>}
    </Section>
  );
}
