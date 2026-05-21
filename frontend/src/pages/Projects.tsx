import { useEffect, useState } from "react";
import type { Project } from "@shared/types";

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

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!projects) return <p>Loading projects…</p>;
  if (projects.length === 0) return <p>No git repos found under projects root.</p>;

  return (
    <ul className="cards">
      {projects.map((p) => (
        <li key={p.path} className="card">
          <h2>{p.name}</h2>
          <p className="muted">{p.branch} · {p.commit?.slice(0, 8)}</p>
          <p>{p.subject}</p>
          <p className="muted">{p.ts}</p>
        </li>
      ))}
    </ul>
  );
}
