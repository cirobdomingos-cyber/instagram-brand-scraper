# Marketing Material Playbook

This is the procedure Claude Code follows when the owner asks for any marketing asset against a scraped brand in `data/<handle>/`. Read this in full before producing output.

## Inputs you can rely on

- `data/<handle>/manifest.json` — engagement stats, top hashtags/mentions, post type mix
- `data/<handle>/profile.json` — full bio, follower count, business category, external URL
- `data/<handle>/posts.json` — array of posts with caption, timestamp, displayUrl, likesCount, commentsCount, hashtags, mentions, type, locationName, childPosts
- `data/<handle>/images/<shortcode>.jpg` — the actual visuals (read with the Read tool, you're multimodal)
- `data/<handle>/profile_pic.jpg`

If `posts.json` is large, read top-by-engagement first (sort by `likesCount`), then the 5 most recent. You rarely need every post.

## Phase 1 — BRAND_DNA extraction (always first)

Before producing any asset, write a `BRAND_DNA` block. Be concrete, not generic. "Warm, inviting" is useless. "Earth tones, hand-lettered serif headers, casual second-person Portuguese with em-dashes" is useful.

Structure:

```
BRAND_DNA
├── identity
│   ├── name, handle, one-line positioning
│   ├── category & implicit competitors
│   └── brand stage (DTC startup / established / personal / institutional)
├── visual_system
│   ├── dominant colors (hex sampled from profile_pic + recent posts)
│   ├── typography mood (serif/sans, vintage/modern, hand-lettered)
│   ├── photography style (flat lay / portrait / candid / studio / UGC)
│   ├── editing treatment (warm/cool, contrast, grain, film vs digital)
│   └── recurring visual motifs (props, framing, layouts, signature shots)
├── voice
│   ├── language(s) and register (formal/casual/slang)
│   ├── sentence rhythm (short bursts vs. long captions)
│   ├── emoji & punctuation conventions
│   └── 3 verbatim caption excerpts that BEST capture the voice
├── content_pillars
│   └── 3–5 recurring themes — each with example post URLs and avg engagement
├── audience
│   ├── inferred demo (age, gender skew, location, income tier) — with evidence
│   ├── psychographic — what they care about, ICK list
│   └── follower-to-engagement signal (eng rate %, top-performing post type)
├── commerce_signals
│   ├── product/service offered (if any), price tier hints
│   ├── conversion mechanics seen (link in bio, DM, WhatsApp, store visit)
│   └── posting cadence (posts/week, days/times)
└── gaps_and_risks
    └── what the data DOESN'T tell us, and where you're inferring
```

## Phase 2 — Asset production

The owner specified the asset type (or you ask once if ambiguous). Format conventions:

| Asset type | Format |
|---|---|
| `instagram_carousel` | One markdown section per slide. Per slide: headline, body, visual direction, alt text. Optional inline SVG mockup of slide 1. |
| `instagram_story` | 3–5 frames. Per frame: copy, sticker/poll suggestions, visual direction. |
| `instagram_reel_script` / `tiktok_script` | Beat-by-beat: on-screen text, voiceover, b-roll cues, hook/payoff structure, hashtags. |
| `launch_post` | Caption + 5 hashtag variants (broad/niche/branded/local/trending) + first-comment text. |
| `press_release` | Standard inverted-pyramid: headline, dateline, lede, 3 body paras, boilerplate, contact. |
| `landing_page` | Self-contained HTML (Tailwind CDN ok). Hero, value props, social proof from real captions, CTA. |
| `one_pager_pdf` | Single-page HTML sized for A4/Letter print, with `@page` CSS. |
| `email_campaign` | 3 subject variants, preview text, full HTML body, plain-text fallback. |
| `logo_concepts` | 3 SVG concepts inline. Each: rationale, color spec, usage notes. |
| `brand_style_guide` | Multi-section HTML doc: logo, palette swatches, type specimens, voice rules, photo dos/don'ts. |
| `ad_creative_set` | 3 concepts × {headline, primary text, description, CTA, visual direction}. Score hook strength 1–10. |
| `brand_audit` | Strengths / weaknesses / opportunities, with references to specific posts. End with a 30-day action plan. |

## Output discipline

Write outputs to `data/<handle>/output/<asset_type>_<YYYYMMDD-HHMM>/`. One folder per generation so iterations don't overwrite each other.

Always end with two blocks:

```
RATIONALE
├── 3 design choices, each grounded in a specific scraped post (cite URL or shortcode)

NEXT_STEPS
├── concrete actions to ship: Figma file to open, Meta Ads campaign to set up, etc.
```

## Hard rules

- **No invented facts.** If the scrape is silent on it, say so and make a defensible inference.
- **Brand voice over generic voice.** Mimic the brand's actual register, not "engaging social media tone."
- **Color discipline.** State the hex values you used. Sample from real images, don't guess.
- **Reuse the brand's recurring patterns.** If they always shoot product flat-lay on linen, your mockups should too.
- **Constraints are absolute.** If the owner said "Portuguese only" or "no discount language," that overrides every other rule here.

## Output order (every time)

1. `GAPS_FLAGGED` — only if scrape is incomplete; one short paragraph.
2. `BRAND_DNA` — full block above.
3. The asset itself, in the format from the table.
4. `RATIONALE`.
5. `NEXT_STEPS`.

## Iteration mode

If the owner asks for "another version" or "tweak X," skip Phase 1 (the brand DNA hasn't changed) and go straight to a new asset folder. Reference the BRAND_DNA from the prior run by path.
