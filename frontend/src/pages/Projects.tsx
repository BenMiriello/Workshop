import { useEffect, useState } from "react";
import type { Project } from "@shared/types";
import { Card, CardGrid, PageHeader, StateBlock } from "../ui";

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/projects/")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then(setProjects)
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <PageHeader title="Projects" sub={projects ? `${projects.length} repos` : undefined} />
      {err ? (
        <StateBlock variant="error" title="Couldn't load projects" hint={<code>/api/projects/</code>} />
      ) : !projects ? (
        <p className="ws-muted">Loading…</p>
      ) : projects.length === 0 ? (
        <StateBlock title="No repos found" hint="No git repositories under the projects root yet." />
      ) : (
        <CardGrid>
          {projects.map((p) => (
            <li key={p.path}>
              <Card>
                <h2 className="ws-card__title">{p.name}</h2>
                <p className="ws-card__meta ws-mono">
                  {p.branch} · {p.commit?.slice(0, 8)}
                </p>
                <p style={{ marginTop: "var(--space-2)" }}>{p.subject}</p>
                <p className="ws-card__meta">{p.ts}</p>
              </Card>
            </li>
          ))}
        </CardGrid>
      )}
    </div>
  );
}
