# Storage and structure model

**Status:** proposal, not ratified. Written 2026-08-19 from two operator design
sessions. If ratified it becomes the substrate that `entity-model-2026-07-18.md`
and the navigation shell both sit on.

**What this decides.** Where a user's files and data physically live, what the
tree in the UI actually is, what things are called, how apps relate to
environments and deployments, the navigation model, and in what order to build
it. It deliberately ignores what is currently built: the question is what the
right system is, and migration is a later, separate problem.

---

## 1. Requirements

Taken from the operator sessions, stated so they can be argued with.

1. Every app needs a directory of files an agent can read and write with
   ordinary tools. `claude -p` needs a cwd, code needs to compile, git needs to
   work, `bwrap` mounts a path.
2. Durable data must not live on the running instance. Wiping the instance disk
   must lose nothing.
3. The UI must present a browsable tree that mixes real directories with
   managed collections (chats, tickets, runs, memory) without the boundary
   feeling arbitrary.
4. It must work from a phone, where there is no local filesystem at all.
5. It must be able to work from a desktop app with local files and offline use,
   and the two must reconcile.
6. Agents run confined. A turn sees one app and what it is explicitly granted.
7. History and reversibility. An agent turn that damages a file must be
   revertable, the same way a chat branch is.
8. Multi-user capable eventually, single-operator now. The design must not
   *require* multi-tenant machinery, but must not preclude it.

---

## 2. Three facts that constrain everything

**Object storage has no tree.** S3 is a flat keyspace. A "folder" is a prefix on
a key with a `/` delimiter, produced by `ListObjectsV2` with a delimiter
argument. There is no rename: renaming a prefix means copying every object under
it and deleting the originals, against a 5,500 request/second/prefix ceiling.
Object tags are a separate, small (10 per object) metadata facility, not a
hierarchy. **Therefore hierarchy cannot live in the blob store.**

**Agents need POSIX.** No agent can be handed a bucket. Something must project
durable state into a real directory.

**Records need queries.** Chats, tickets and runs are relational, already
branch, and need indexed lookup. A file per chat was never viable.

The design is the resolution of these three, and it is not novel: it is what
everyone building this shape has converged on.

---

## 3. What comparable systems actually do

Researched 2026-08-19. Three independent 2026 systems solving close to our exact
problem, plus the older general-purpose precedents.

**Cursor "Continuity"** (their git storage layer for cloud agents). Source of
truth is a **write-ahead log in S3-compatible object storage**. On-disk
repositories are explicitly "warm caches". Repos are placed by rendezvous
hashing rather than a routing table; if a repo is not on the machine, it is
**materialized from the WAL on access**. Idle repos are garbage-collected from
disk and re-materialized later. They report millions of small agent-created
repositories needing one or zero persistent replicas. The problems they cite in
plain server-side git are packfile random I/O, reference transactions needing
distributed consensus, and multi-replica state sync.

**Cloudflare Computer** (persistent stateful environments for agents, Aug 2026,
early preview). A **SQLite-based filesystem** shared between V8 isolates and
containers, exposed to containers over **FUSE**. It integrates with git repos,
storage buckets and arbitrary files, i.e. git is an import/export integration,
not the store. All filesystem operations are gated and observed.

**Turso AgentFS** ("Towards a Disaggregated Agent Filesystem on Object
Storage"). Requirements named: portability across ephemeral compute,
snapshotability of the whole runtime, queryability of state via SQL, and
multi-agent collaboration on one filesystem. Architecture: metadata (inodes,
dirents) in SQLite, mutations in a WAL, **object storage as source of truth**, a
coordinator doing lazy load and background sync, POSIX exposed via FUSE or NFS.
It calls out that a WAL-based model gives time-travel and branching naturally
where a conventional distributed filesystem stores only current state.

**JuiceFS** (general-purpose, mature). A POSIX filesystem whose **metadata is in
Postgres/Redis/MySQL** and whose **data is chunked into object storage**. The
mapping from chunks and slices to files lives entirely in the metadata engine.

**Dropbox.** Files split into ~4MB chunks, each hashed with SHA-256, the hash
serving as both identity and the S3 key, giving deduplication for free. The
**hierarchy is in Postgres**, chosen over a NoSQL store precisely because
folder containment and ordered chunk lists are referential constraints.

**Git.** Content-addressed immutable objects (blob, tree, commit), refs, and a
working copy materialized from them. The original version of this architecture.

**The counter-case, which matters.** "Package managers keep using git as a
database, it never works out" catalogues the failures: Cargo's index causing
multi-minute delta resolution, Homebrew users pulling 331MB and ~1GB `.git`
directories, CocoaPods clones taking minutes and drawing GitHub CPU rate limits,
nixpkgs at 83GB nearly forced read-only, git-backed wikis becoming "too slow to
be usable" with seconds per directory click. Every one of them ended up building
an HTTP index or CDN in front (Cargo sparse protocol, GOPROXY, CocoaPods CDN,
Homebrew JSON). Git also has no constraints, no locking, no indexes, and its
answer to a schema migration is rewrite history and force a re-clone.

**The synthesis is sharp and consistent:**

- Git is wrong as a **query layer or client-facing read path**. Nobody should
  ever clone a history to read a file.
- Git's **object model** is right as a versioned content store, and its **merge
  engine** is the expensive part nobody should reimplement.
- The **durable form is a log plus content-addressed objects in object
  storage**. Every 2026 system agrees on this, whether the log describes git
  objects (Cursor) or filesystem inodes (Turso, Cloudflare).
- **On-disk state is a cache.** Named as such by Cursor, implied by the
  materialize-on-access model in all three.

---

## 4. The storage model

Three stores and one index.

### 4.1 Blobs

Immutable bytes in object storage, keyed by `sha256` of content. Never mutated,
never renamed, deduplicated for free. Uploads, attachments, images, PDFs, build
artifacts, model outputs.

Layout is prefix-per-tenant (`s3://bucket/<tenant>/blobs/<hash>`) with IAM
policies scoped to the prefix, which is where isolation actually lives rather
than in application code. Transfer is by **presigned URL in both directions**,
so bytes never pass through the app server.

Because keys are content hashes, nothing in this store is ever renamed or moved,
which is exactly the operation object storage is bad at. That is not a
coincidence; it is why content addressing is the right key.

### 4.2 Repos

**One git repository per app**, holding that app's authored file tree: code,
docs, memory notes, skills, personas, configuration the user writes.

Git is chosen for four reasons and not for familiarity:

1. History and revert. Requirement 7 is a version control requirement.
2. Merge. Requirement 5 (desktop reconciliation) is a merge problem, and merge
   is the single most expensive thing to write correctly.
3. Branching gives per-run isolation for free. Two agents working one app get
   two worktrees, not a lock.
4. Agents run real `git` inside their working directory anyway. Any other store
   means a second, competing history.

The package-manager failure mode is avoided by construction: **git is never the
read path.** Reading a file in the UI is an API call served from the index and
the blob cache. Cloning happens only when materializing a working copy for
execution, and only for one app.

Durability follows Cursor: the authoritative form of a repo is its objects and
ref updates persisted to object storage. A bare repo on a disk is a cache and
may be garbage-collected and re-materialized. At current scale this is a mirror
after each ref update; the architecture does not change when it becomes a real
WAL.

### 4.3 Records

Postgres. Chats, tickets, runs, agents, grants, environments, deployments,
refs, and the node index. Everything relational, everything queried, everything
that needs constraints and migrations.

### 4.4 The node index

One Postgres table that is the single navigable, addressable and searchable tree
across all three stores.

```
node(id, app, parent_id, name, kind, target, sort_key)
kind ∈ {app, dir, file, collection, record}
```

`kind` is the whole design, because **the verbs available on a node are a
function of its kind**:

- `dir`, `file`: real. Support move, rename, create, delete. Rename is an
  `UPDATE` of one row. `target` is a path within the app's repo.
- `collection`: an anchor (Chats, Work, Memory, Runs, Deploys). It has **no
  stored children**. Its contents resolve by query at read time.
- `record`: one row in a typed table, reached through a collection.
- `app`: an app node, whose parent edge is the containment tree that
  `services/scope_tree.py` already owns.

Nothing pretends to be a file. You cannot `mv` a chat into a directory, because
that operation does not exist on that kind. This is the rule that stops the
real-versus-virtual boundary from feeling arbitrary, and it is the same rule
Android's Storage Access Framework and Apple's File Provider use: a typed
document node whose backing may be disk, database or cloud, and no consumer may
assume a path.

Two properties matter more than the schema.

**The index is derived and rebuildable, never a second source of truth.**
Structure is authored in exactly one place (a git commit, or a record's app
column) and *projected* into the index. If the index disagrees with a store, it
is dropped and rebuilt. This is a direct response to the chat mirror, which went
from 63 of 63 clean to 67 of 70 drifted within hours because it was a second
authority that could disagree and had no procedure for being right.

**The index stores only authored structure.** Collection contents resolve live.
So the table holds thousands of rows, not millions, and the record half of the
system cannot drift at all, because nothing about it is stored twice. Only one
projector (git tree to nodes) ever needs to run, and it runs on ref change.

### 4.5 Materialization

The instance disk holds exactly one kind of thing: **materializations**. A
materialization is a checkout of one app at one ref into a real directory. It is
disposable by definition, cache-evictable, addressed by `(app, ref)`, and it is
the only thing a turn's `bwrap` mounts.

One concept does a lot of work:

- The instance becomes stateless. Requirement 2 is satisfied structurally rather
  than by discipline.
- Confinement gets a natural unit. A grant becomes "mount this other app's
  materialization read-only", which is a real capability rather than a rule in a
  prompt (see SEC-A2, which correctly says scope is not a security boundary
  today).
- "The agent's working directory" becomes explicit rather than ambient.
- The read-only platform floor is just an app whose ref is pinned to a shipped
  commit and whose materialization is mounted read-only. It stops being a
  convention.
- Two concurrent runs on one app get two materializations at two refs. The race
  that has already bitten this repo becomes structurally impossible rather than
  locked against.

**Full checkout, not FUSE, at least initially.** FUSE buys lazy loading of huge
trees, which is a problem we do not have (personal-scale repos), and costs a
userspace daemon in the hot path of every syscall an agent makes, plus a known
pathological interaction with overlay drivers. Revisit only if materialization
latency is measured to be a problem.

---

## 5. Apps, environments, deployments

The open question was whether the file tree nests inside an environment, whether
memory lives inside a development environment, and whether opening a project
should default to production. The answer is that these are **three orthogonal
axes** and nesting any two produces a combinatorial tree with no natural
default.

**App** is ownership. It answers "whose is this". It is the containment tree:
Workshop at the root, apps under it, agents either under an app or at the root.
An app owns one repo and several collections.

**Ref** is version. It answers "which state of it". A branch, a commit, or the
working state of a materialization. The file tree is always viewed *at a ref*.

**Environment** is a deployment target. It answers "where does this run". A
named binding of `(config, secrets, runtime resources)` that a build is deployed
into. This matches what the platforms do: a Railway environment is a full
isolated copy of a project's services and configuration, with production
provided by default; a Vercel environment is a deployment target with previews
derived from branches.

The consequences:

- **Opening an app shows its source at the current working ref.** Not
  production. Production is not a place you edit; it is a deployment you inspect.
  The base case is that you run applications in your own environment for your own
  usage, and it falls out: the default environment is local, and production is
  opt-in.
- **Memory belongs to the app, not to an environment.** Knowledge about an app
  does not fork per deployment target. If it did, the same lesson would exist in
  four copies and immediately drift. Memory is a directory-shaped page of the
  app, versioned with the repo.
- **Environments are records, deployments are records.** A deployment references
  an environment and a commit. Neither is a node in the file tree. They appear as
  a `Deploys` collection.
- **Environments are not apps.** Do not put `production` in the app tree.

---

## 6. Vocabulary

Naming from scratch. The observation that forced this: "scope" describes a
permission boundary, not a thing, and what the user actually has is an app that
becomes a project when they work on it.

**App.** The primary entity, and the only container the user names. It has a
name, an icon, a repo, pages, and a place in the tree. **"Project" is not a
second entity; it is the build view of an app.** Today the platform treats Apps
and Projects as distinct linkable concepts, and collapsing them removes an
entire axis of confusion: every app can be used and every app can be worked on,
and which one you are doing is a page you are on, not a different object.

**Agent.** An app-shaped entity with no user-facing UI. It sits at the root when
unattached and under an app when bound to one. Kept as its own kind rather than
folded into App, because its page list genuinely differs (Runs, Schedule) and
because an agent bound to an app is a child of it.

**Page.** What the earlier handoff called a "section". Chats, Files, Memory,
Work, Agents, Deploys, Settings. The user's own word, and it is better: these
read as pages, which is exactly what the underlined-tab treatment was for.

**Home.** The root. Not "the workshop scope". Where you start chats and see
everything.

**Scope.** Internal only. The addressing and permission boundary of an app.
Never appears in the UI, the same way Kubernetes says namespace internally while
OpenShift says project to users. `services/scope_tree.py` keeps its name.

**Ref.** Internal. The UI says "branch" or names the version.

**Node.** Internal. Never user-facing.

Unchanged and already right: Chat, Ticket, Run, Note, Deploy, Memory.

---

## 7. What the file tree means in the UI

Is each app its own file tree, or does each app have pages, some of which
contain file trees? **The latter, and the split is at a named boundary.**

- **The dock and the page column are the app structure.** They are the Jump
  axis. They must stay scannable and usable as a phone drawer. Putting a repo in
  them turns them into a code editor sidebar and destroys the one job they have.
- **File trees live inside directory-shaped pages** (Files, Memory) as that
  page's own navigator, with a root change on entry and a way back out. This is
  Finder entering a folder, VS Code opening a folder, GitHub's file browser
  inside the Code tab.

So there *is* one continuous tree, and it is the node index. What changes at the
app-to-repo boundary is not the model but the **verb set**, which the node
`kind` already carries. That is the honest version of "the whole app is one
tree", and it is why "make databases look like folders" is rejected: VS Code had
to add a per-extension `capabilities.virtualWorkspaces` flag precisely because a
large fraction of extensions broke when the "files" were not files. Virtualizing
files is a tax every consumer pays; projecting typed resources is not.

---

## 8. Navigation model

Settled 2026-08-19. The earlier navigation handoff is adopted for its layout
language and its Up-versus-Back discipline, and replaced on structure.

### 8.1 What survives from the earlier handoff

Up is not Back (Back is history and belongs to the browser and the OS; Up is
structural and belongs to the page). The three axes test for whether a control
belongs: Lateral between pages, Vertical into and out of an item, Jump to
anywhere. No breadcrumb. Underlined tabs rather than pills. One search index with
an ask row above the results and current-app-first ranking. Pages are a function
of app kind, so the list length itself signals what kind of thing you are in.

### 8.2 Desktop: three columns

**Column one, the Dock.** Narrow, fixed, icons only, first letter in a tile as
the fallback when an app has no icon. Home at the top, apps below, Settings at
the bottom. The current app reads as selected.

Tapping an app in the dock **opens the app**, because the first page of an app
is the app itself. The dock behaves like a real dock.

Tapping the **already-selected** app toggles column two open and closed.

**Column two, the Page column.** Wider than the dock. The app's pages, indented
under the app name. This is where you move between Chats, Files, Memory, Work,
Deploys, Settings.

**Column three, content.** The page.

There is **no top toolbar on desktop.** The dock and page column carry it.

An earlier idea, a single wide app list that collapses to a narrow bar when you
select something, is **superseded** by the three-column model.

### 8.3 Desktop: the file navigator

When the Files page is open, column two is **replaced** by the file tree, and
the transition is specific:

- The Files row rises to the top of column two and stays as a header.
- The file tree slides in from the right over the rest of the page list.
- The header row carries a back control. Going back restores the page list and
  **does not change the content**: you are still in the file editor.
- With the page list restored, the Files row still reads as active and carries a
  right-pointing chevron to go back into the tree. The two-way toggle is
  symmetric.

### 8.4 Per-app state

Each app remembers which page was last open. Returning to an app returns you to
where you were, not to a fixed landing page.

### 8.5 Chats

No global Chats button in the dock. Chats start from Home. Each app has its own
Chats page.

### 8.6 Mobile

Bar one and the page-tab bar from the earlier handoff survive, with scope
renamed to app. Home sits at the top left before the app picker.

The app picker opens a left overlay drawer. **The drawer lists apps only, one
column.** The page tab bar remains the page switcher.

The alternative, mirroring the desktop's two columns inside the drawer, was
considered and is not recommended: it puts two page switchers on the smallest
screen, one of which costs an extra tap. The tab bar wins on both count and
distance.

On the Files page the file tree is the page content and drills in place, with Up
walking the tree. It does not take over the tab bar.

### 8.7 Structural changes the model requires

1. A **Files** page, which the earlier handoff has nowhere.
2. A **ref indicator** on the title line, since the tree is always at a ref.
3. The tree shows **containment**, not grouping by kind. Kind groups survive
   only at the root, where containment has nothing to say.
4. **Up from a detail targets the item's home**, not the app you were browsing.
   If a list is filtered wider than the current app, which is the point of the
   filter, the other behaviour gives a wrong Up.
5. The unspecified **Filter** control is the widening control and where saved
   views live. Views are not pinned into the dock.
6. Search gains a **Files** group.
7. The router becomes `/<app>/<page>/<detail>`, a full rewrite of today's flat
   global routes and the largest single item in the shell.

---

## 9. State design

**The URL owns location.** `(app, page, detail, ref)`. Deep-linkable,
back-button-correct, shareable. Not React state.

**The server owns records.** Postgres is authoritative. The client keeps a local
store and reconciles; the server provides total ordering and its timestamp wins
on conflict. This is the Linear and Figma sync-engine shape, deliberately
simpler than vector clocks, and it is what makes the desktop client a
configuration rather than a rewrite.

**The client caches file content by hash.** Because blob keys are content
hashes, the cache is trivially correct and never invalidates wrongly.

**UI preferences are local and named.** Page column open or closed, tree
expansion, last filter, and the per-app last-page memory. Never in the URL,
never on the server.

**Materializations are ephemeral state with an owner.** Created by a turn or a
session, garbage-collected on idle, never referenced by anything durable except
by `(app, ref)`, which can always be recreated.

---

## 10. Code quality rules this implies

Checkable things rather than intentions.

1. **One authority per fact.** Anything spanning stores is derived and
   droppable. A gate should fail on a second writer to a projected field. The
   existing `_PROJECTED_KEYS` pattern in the ticket store is the shape.
2. **One owner per concept.** `scope_tree.py` owns the app tree. A new `nodes`
   module owns the index. A new `materialize` module owns working copies.
   Nothing else may spell an app path by string concatenation;
   `paths.scope_root` is already this rule and is enforced by the no-raw-paths
   gate.
3. **Verbs are typed by node kind.** The API must not expose a generic
   `move(node, parent)` that silently no-ops on a collection. Refuse loudly.
4. **No raw operating-system paths in agent-facing text or UI copy.** Already a
   gate. It becomes load-bearing here, because under materialization a path is
   meaningful only with its `(app, ref)`.
5. **Every store boundary is crossed in one place.** One blob client, one repo
   client, one materializer. Not three call sites each.

---

## 11. Stages

Each stage is independently useful and none requires the next to exist.

**Stage 1: the node index.** One derived table, one address per node, projectors
over the stores as they exist today. First, deliberately: every screen then
builds against the index, and every later storage change happens underneath it
without touching a screen. *Done when* the dock, the page column, search, and
every `workshop:` ref resolve through one query, and dropping and rebuilding the
table changes nothing observable.

**Stage 2: the materialization boundary.** An app's working directory becomes an
explicit checkout with a lifecycle, addressed by `(app, ref)`, and the only
thing a turn mounts. *Done when* a turn's directory can be destroyed and
recreated with nothing lost, `bwrap` mounts exactly it, and two runs on one app
no longer contend.

**Stage 3: bytes off the instance.** Object storage becomes the canonical byte
store. Uploads and attachments first, since they are already opaque blobs and
carry the least risk, with presigned direct transfer. Then per-app repos, with
objects and ref updates mirrored to object storage and on-disk repos demoted to
caches. *Done when* wiping the instance disk loses nothing, proven by doing it.

**Stage 4: the shell.** The navigation model in section 8, on the index,
including the Files page the model now supports.

**Stage 5: environments and deployments as records.** An app gets named
environments, a deployment references an environment and a commit, and the
Deploys page stops being a placeholder.

**Stage 6: desktop client.** A materialization holder plus a record replica plus
sync. Files reconcile through git, records through the sync engine. This is
where the free-versus-paid line lands: local compute with your own key is free,
canonical storage and continuity and the phone are paid.

---

## 12. Open questions

Named rather than hidden.

1. **Where do bare repos physically sit before the WAL exists?** A durable
   volume is the pragmatic answer at current scale and is what makes stage 3's
   second half straightforward. The Cursor shape (log in object storage, disk as
   cache) is the target; the question is when the intermediate stops being good
   enough.
2. **Is one repo per app right, or one per app-group with children as
   subdirectories?** Per-app keeps confinement and grants clean. Per-group makes
   cross-app refactors natural. Leaning per-app because confinement is
   requirement 6 and refactors are rare.
3. ~~Do memory notes live in the repo or as records?~~ **DECIDED 2026-08-19:
   files.** Operator: "I think files. Want user to be able to write or edit them
   like files at least." So memory notes are real files in the app's repo,
   versioned and diffable, and the user may edit them directly. The querying and
   injection need is served by the node index and the semantic index projecting
   from them, not by a second copy in a table. That makes Memory a
   directory-shaped page, identical in mechanics to Files.
4. **Merge policy for desktop sync.** Git merges files. Records take
   server-wins. What happens when a record and a file disagree about the same
   thing (a ticket body and a doc) is not specified.
5. **Large binaries.** Blobs handle uploads. A generated multi-gigabyte artifact
   inside an app repo does not, and git's answer (LFS) is a known tax. Probably
   artifacts are blobs referenced from the repo and never committed into it.
6. **Does collapsing Project into App lose anything?** An app you never run and
   only work on, and an app you run and never edit, are the two ends. If a real
   case needs them separate, this is the decision to revisit first.
