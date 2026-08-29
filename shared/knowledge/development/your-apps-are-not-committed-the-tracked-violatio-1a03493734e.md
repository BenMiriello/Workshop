<!-- author: self; chat: 2c44eaf71ef14a3f83c96d119787ba9f -->
Measured 2026-08-24 on clean HEAD, and worth checking before anyone panics about this again.

`git ls-files apps/ | wc -l` returns **0**. `/apps/` has been gitignored since 2026-08-20 (`.gitignore:160`), together with `/agents/apps/`, `/backend/mcp/plaid/` and `/commute/`. The ~30,000 files and 2.2GB under `apps/` are UNTRACKED working-tree data on this one machine. A clone by anyone else contains none of it.

What IS tracked, per `scripts/check_no_app_names.py`: content 102 tracked files that ARE app source (birds 48, commute 21, finance 9, notes-app 9, budget 7, location 5, health 3), imports 20, literals 305. Mostly React pages under `frontend/src/pages/apps/` and service modules under `backend/services/`.

Do not conflate the working tree with the repository when reporting this. Saying app files are 'in the platform tree' reads as 'committed' and has caused a false alarm. State `git ls-files` first.

The remaining work is stages 4b (promote 11 platform modules into the kit), 5 (the runtime app loader, which is the real blocker), and 7 (extraction). See docs/platform-redesign/app-separation-why-it-recurs-2026-08-24.md.