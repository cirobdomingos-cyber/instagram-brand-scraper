# Instagram Brand Scraper — project guide

## What this is
A Railway-deployed web app that lets allowlisted users:
1. Type an Instagram `@handle` → backend scrapes profile + posts + images via Apify.
2. Click an asset type (carousel, landing page, brand audit, ad set, etc.) → backend calls Anthropic with a brand-strategy playbook → renders the asset in-browser.

Two-phase Anthropic flow: `BRAND_DNA` extracted once per handle (cached on disk), then asset generation reuses it cheaply. Prompt caching marks the system prompt + scraped images as ephemeral so back-to-back asset requests for the same brand stay cheap.

## Architecture

```
static/                  # SPA — vanilla JS, Tailwind CDN, Google GSI
├── index.html
└── app.js
app/
├── main.py              # FastAPI: routes, static serve, deps wiring
├── auth.py              # Google ID token verify + email allowlist
├── scraper.py           # Apify calls + image downloads
├── generator.py         # Anthropic BRAND_DNA + asset, with prompt caching
├── storage.py           # disk paths, atomic writes
└── prompts.py           # playbook constants (system prompt, asset formats)
scrape.py                # CLI fallback — calls app.scraper directly
data/<handle>/           # gitignored, persisted in Railway volume at /data
├── profile.json, posts.json, manifest.json, profile_pic.jpg
├── brand_dna.json       # Anthropic-extracted, cached
├── images/
└── output/<asset>_<ts>/{asset.md, asset.html?, asset.svg?, meta.json}
```

## Auth
- `GOOGLE_CLIENT_ID` set → real Google OAuth (frontend GSI, backend verifies JWT against Google's public keys)
- `GOOGLE_CLIENT_ID` unset → mock mode (frontend asks for email, backend trusts `X-Mock-Email` header iff in allowlist). Frictionless for local dev.
- `ALLOWED_EMAILS` env var (comma-sep) gates everything. `OWNER_EMAIL` is auto-added.

## Running locally

```bash
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill APIFY_API_TOKEN + ANTHROPIC_API_KEY at minimum
uvicorn app.main:app --reload --port 8000
# → http://localhost:8000  (mock mode if GOOGLE_CLIENT_ID empty)
```

## Deploying to Railway

1. Push this repo to GitHub.
2. Railway → New Project → Deploy from GitHub → pick repo.
3. Variables tab: paste `APIFY_API_TOKEN`, `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `ALLOWED_EMAILS`, `DATA_ROOT=/data`.
4. Volumes tab: mount `/data` (1 GB plenty).
5. Google Cloud Console → Credentials → add the Railway URL to "Authorized JavaScript origins."
6. Done. Public URL is your `*.up.railway.app`.

## Cost shape
- Apify: ~$0.005/scrape on the actor (~50 posts) + image downloads are free
- Anthropic: BRAND_DNA ~$0.05 per brand (one-time, cached); each asset ~$0.02 (Sonnet) or $0.10 (Opus, only `logo_concepts` + `brand_style_guide`)
- Worst case for a friend who scrapes 5 brands and generates 3 assets each: ~$0.50

## What this is NOT
- Not the events scraper from `<workspace>/reroot/`. That one writes to a DB and extracts Curitiba events. This one is brand-marketing-only.
- Not a posting bot. We produce material; the user ships it.
