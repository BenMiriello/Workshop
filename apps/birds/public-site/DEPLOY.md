# Public site deploy (Cloudflare Pages + R2)

Static page (`index.html` + `app.js` + `style.css`) plus one Pages Function that
serves the sanitized dataset from R2 at `/birds-public.json`. The page reads only
that file. The sensitive data is never sent here, so a bug in this site cannot
leak audio, clips, or coordinates.

## One-time setup

```bash
# 1. Create the R2 bucket the publish job writes to.
npx wrangler r2 bucket create yard-birds-public

# 2. Create the Pages project (first deploy also creates it).
cd apps/birds/public-site
npx wrangler pages project create yard-birds --production-branch main

# 3. Bind the bucket to the Pages project so the Function can read it:
#    Cloudflare dashboard -> Pages -> yard-birds -> Settings -> Functions
#    -> R2 bucket bindings -> add: variable name BIRDS_BUCKET = yard-birds-public
```

## Deploy

```bash
cd apps/birds/public-site
npx wrangler pages deploy . --project-name yard-birds
```

## Data flow

```
BirdNET-Go SQLite (private, on Brain)
        |  read-only
        v
backend /birds/public   <-- sanitizer: species/counts/day-times/confidence/first-seen ONLY
        |  curl (publish.sh, hourly timer)
        v
R2: yard-birds-public/birds-public.json
        |  Pages Function (BIRDS_BUCKET binding)
        v
public page /birds-public.json  ->  index.html renders it
```

## Local preview

```bash
cd apps/birds/public-site
# point the page at a locally-produced sanitized file:
cp "$WORKSHOP/.runtime/birds-public.json" ./birds-public.json
python3 -m http.server 8088
# open http://localhost:8088/?src=./birds-public.json
```
