# Instagram Brand Scraper

Give it an Instagram handle. Get back the brand's DNA as a structured document, then generate
marketing assets that sound and look like that brand.

Built for a small circle of allowlisted users. Deployed on Railway. Python, FastAPI, Apify,
Claude with vision and prompt caching.

## The business problem

A marketer or agency onboarding a new client spends the first days reverse-engineering the
brand from its Instagram: colours, typography mood, voice, audience, what performs. That work
is repetitive, and the output usually lives in someone's head. This app does the extraction
once, writes it down as `BRAND_DNA`, and reuses it for every asset after that.

## What it does

1. **Scrape.** Type `@handle`. The backend pulls the profile, the last 50 posts and their
   images through the Apify Instagram actor. Nothing touches Instagram directly.
2. **Extract.** Claude reads the profile, the top posts by engagement and the images, and
   returns a `BRAND_DNA` JSON: identity, visual system, voice with verbatim excerpts,
   audience, offer and conversion mechanics. Cached on disk per handle.
3. **Generate.** Pick an asset type. The generator reuses the cached DNA and produces the
   asset as Markdown, HTML or SVG, rendered in the browser and downloadable as a bundle.

Asset types: Instagram carousel, story, reel script, TikTok script, launch post, press
release, landing page, one-pager, email campaign, logo concepts, brand style guide, ad
creative set, brand audit.

## Architecture

```mermaid
flowchart LR
    U[Allowlisted user] -->|Google sign-in| SPA[Vanilla JS SPA]
    SPA --> API[FastAPI]
    API -->|@handle| APF[Apify Instagram actor]
    APF -->|profile, posts, images| DISK[(Railway volume /data/handle)]
    API -->|profile + top posts + images| DNA[Claude: extract BRAND_DNA]
    DNA -->|brand_dna.json, cached| DISK
    API -->|asset type + cached DNA| GEN[Claude: generate asset]
    GEN -->|md / html / svg| DISK
    DISK --> SPA
```

Two-phase Claude flow. Extraction runs once per brand and is the expensive call because it
carries images. Generation is cheap because it only carries the DNA text. The system prompt
and the scraped images are marked `cache_control: ephemeral`, so back-to-back asset requests
for the same brand pay roughly a tenth of the input cost on the cached block.

Model routing: Sonnet for extraction and most assets. Opus only for logo concepts and the
style guide, where design coherence matters more than price.

## Layout

```
app/
  main.py        FastAPI routes, static serving, auth wiring
  auth.py        Google ID token verification, email allowlist, signed session cookie
  scraper.py     Apify calls, image downloads, manifest with engagement stats
  generator.py   BRAND_DNA extraction and asset generation, prompt caching, model routing
  cataloger.py   file catalog for the download bundle
  storage.py     data paths, atomic writes
  prompts.py     system prompt, asset formats, BRAND_DNA schema
static/          index.html, app.js (Tailwind CDN, Google GSI)
scrape.py        CLI: same scrape without the web app
PROMPT.md        the playbook the prompts follow, readable by humans
data/<handle>/   gitignored; profile.json, posts.json, images/, brand_dna.json, output/
```

## Run locally

```powershell
py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # APIFY_API_TOKEN and ANTHROPIC_API_KEY at minimum
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. With `GOOGLE_CLIENT_ID` unset the app runs in mock mode and asks
for an email instead of a Google login. Only emails in `ALLOWED_EMAILS` get past the API.

CLI only:

```powershell
py -3.12 scrape.py @handle --posts 50
```

## Deploy on Railway

One service, Nixpacks build, start command in `railway.toml`. Mount a 1 GB volume at
`/data` and set `DATA_ROOT=/data`. Variables: `APIFY_API_TOKEN`, `ANTHROPIC_API_KEY`,
`GOOGLE_CLIENT_ID`, `ALLOWED_EMAILS`, `SECRET_KEY`. Add the Railway URL to the OAuth client's
authorized JavaScript origins.

## Cost shape

| Step | Cost |
| --- | --- |
| Apify scrape, 50 posts | about $0.005 |
| BRAND_DNA extraction, once per brand | about $0.05 |
| One asset on Sonnet | about $0.02 |
| One asset on Opus | about $0.10 |

Five brands with three assets each lands under $1.

## What it is not

Not a growth tool and not a scraper for accounts you do not have a reason to study. It reads
public profiles through a paid, rate-limited actor, keeps data on a private volume, and
serves only allowlisted users.
