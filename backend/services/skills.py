"""SKILL.md skill store (Phase 5) — portable instruction bundles.

Adopts the Agent Skills open standard: a skill is a folder containing a
`SKILL.md` with YAML frontmatter (`name`, `description`, ...) followed by a
markdown body of instructions, plus optional scripts/resources beside it.

Scopes mirror the knowledge store (files are the source of truth, git-tracked):
  - `workshop`       -> shared/skills/
  - `project:<slug>` -> projects/<slug>/skills/
  - `app:<name>`     -> apps/<name>/skills/
  - `agent:<id>`     -> .runtime/agents/<id>/skills/

Two consumers:
  - **Builders** (Claude Code): `link_into_cwd()` symlinks the in-scope skill
    folders into `<cwd>/.claude/skills/<name>` so the CLI discovers them
    natively (free — no prompt tokens until the model opens one).
  - **Assistants** (data mode, we own the prompt): `descriptions_block()` puts
    the name+description of each available skill into the cache-stable prefix,
    and `get_skill()` loads a full body on demand when the model asks via the
    use_skill action (see routes/chat.py).

`available_for_scope` = the scope's own skills PLUS the workshop baseline
(own-scope wins on a name collision). Same context-hygiene framing as knowledge:
it decides what a chat sees ambiently, not a security wall.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from services import knowledge

# Skill folder name: kebab-case slug, same discipline as Claude Code skills.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _scope_skills_dir(scope_key: str, *, create: bool = False) -> Path:
    """The skills directory for a scope. Mirrors knowledge.scope_dir but with a
    `skills/` leaf and without the eager mkdir side effect unless asked."""
    kind, slug = knowledge._parse_scope(scope_key)
    root = knowledge._root()
    if kind == "workshop":
        d = root / "shared" / "skills"
    elif kind == "project":
        d = root / "projects" / slug / "skills"
    elif kind == "app":
        d = root / "apps" / slug / "skills"
    else:  # agent
        d = knowledge._runtime() / "agents" / slug / "skills"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def parse_skill_md(text: str) -> dict:
    """Split a SKILL.md into `{name, description, body}`. Frontmatter is the YAML
    block between the first pair of `---` fences; we read the simple `key: value`
    lines we care about (name, description) without a YAML dependency. `body` is
    everything after the closing fence (the instructions)."""
    name = ""
    description = ""
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            fm = stripped[3:end]
            after = stripped[end + 4:]
            body = after.lstrip("\n")
            for line in fm.splitlines():
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip().strip('"').strip("'")
                if key == "name":
                    name = val
                elif key == "description":
                    description = val
    return {"name": name, "description": description, "body": body.strip()}


def _read_skill_folder(folder: Path) -> dict | None:
    md = folder / "SKILL.md"
    if not md.is_file():
        return None
    try:
        parsed = parse_skill_md(md.read_text(errors="replace"))
    except OSError:
        return None
    # The folder name is the canonical skill id; frontmatter name is a label.
    return {
        "name": folder.name,
        "label": parsed["name"] or folder.name,
        "description": parsed["description"],
        "body": parsed["body"],
        "path": str(folder),
    }


def list_skills(scope_key: str) -> list[dict]:
    """Skills defined directly in this scope (no workshop fallback), sorted by
    name. Each: `{name, label, description, path}` (no body)."""
    d = _scope_skills_dir(scope_key)
    if not d.is_dir():
        return []
    out = []
    for child in sorted(d.iterdir()):
        if not child.is_dir():
            continue
        s = _read_skill_folder(child)
        if s:
            out.append({k: s[k] for k in ("name", "label", "description", "path")})
    return out


def get_skill(scope_key: str, name: str) -> dict | None:
    """Full skill (incl. body) by name, searching the scope then the workshop
    baseline. Returns None if not found or the name is invalid."""
    if not SKILL_NAME_RE.fullmatch(name or ""):
        return None
    for sk in (scope_key, "workshop"):
        folder = _scope_skills_dir(sk) / name
        s = _read_skill_folder(folder)
        if s:
            return {**s, "scope": sk}
    return None


def available_for_scope(scope_key: str) -> list[dict]:
    """The ambient skill set for a chat in this scope: the scope's own skills
    plus the workshop baseline. Own-scope wins on a name collision. Each:
    `{name, label, description, path, scope}` (no body)."""
    seen: dict[str, dict] = {}
    scopes = [scope_key, "workshop"] if scope_key != "workshop" else ["workshop"]
    for sk in scopes:
        for s in list_skills(sk):
            if s["name"] not in seen:
                seen[s["name"]] = {**s, "scope": sk}
    return sorted(seen.values(), key=lambda s: s["name"])


def descriptions_block(scope_key: str) -> str:
    """Compact ambient block for the cache-stable prompt prefix: one line per
    available skill (label — description). Empty string when there are none."""
    skills = available_for_scope(scope_key)
    if not skills:
        return ""
    lines = [f"- {s['name']}: {s['description'] or s['label']}" for s in skills]
    return "\n".join(lines)


def put_skill(scope_key: str, name: str, description: str, body: str, *, label: str = "") -> dict:
    """Author/overwrite a skill in a scope. Writes `<scope>/skills/<name>/SKILL.md`
    with frontmatter + body. Returns the stored skill (no body)."""
    if not SKILL_NAME_RE.fullmatch(name or ""):
        raise ValueError(f"invalid skill name: {name!r} (kebab-case slug required)")
    folder = _scope_skills_dir(scope_key, create=True) / name
    folder.mkdir(parents=True, exist_ok=True)
    fm_name = (label or name).replace('"', "'")
    desc = (description or "").replace("\n", " ").strip().replace('"', "'")
    content = f"---\nname: {fm_name}\ndescription: \"{desc}\"\n---\n\n{body.strip()}\n"
    (folder / "SKILL.md").write_text(content)
    return {"name": name, "label": fm_name, "description": desc, "path": str(folder), "scope": scope_key}


def delete_skill(scope_key: str, name: str) -> bool:
    if not SKILL_NAME_RE.fullmatch(name or ""):
        return False
    folder = _scope_skills_dir(scope_key) / name
    if not (folder / "SKILL.md").is_file():
        return False
    import shutil
    shutil.rmtree(folder)
    return True


def link_into_cwd(cwd: str | Path, scope_key: str) -> list[str]:
    """Symlink the in-scope skills (scope + workshop baseline) into
    `<cwd>/.claude/skills/<name>` so Claude Code discovers them natively. Never
    clobbers an existing entry (a hand-placed skill wins). Returns the names
    linked this call. Best-effort: a failure on one skill doesn't abort the rest.
    """
    cwd = Path(cwd)
    dest_root = cwd / ".claude" / "skills"
    linked: list[str] = []
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return linked
    for s in available_for_scope(scope_key):
        src = Path(s["path"])
        dest = dest_root / s["name"]
        if dest.exists() or dest.is_symlink():
            continue  # hand-placed or already-linked skill wins
        try:
            os.symlink(src, dest, target_is_directory=True)
            linked.append(s["name"])
        except OSError:
            continue
    return linked
