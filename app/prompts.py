"""
Playbook constants. Two long strings:

  SYSTEM_PROMPT — identity + hard rules. Marked cache_control=ephemeral
  upstream so it's reused across requests for the same brand.

  BRAND_DNA_PROMPT_TEMPLATE — phase 1: extract structured BRAND_DNA from
  the scraped payload. Returns JSON.

  ASSET_PROMPT_TEMPLATE — phase 2: produce the requested asset given the
  cached BRAND_DNA + user inputs.

Asset format conventions live here too — single source of truth.
"""

SYSTEM_PROMPT = """\
You are a senior brand strategist + art director. The owner gives you the
complete Instagram dump for one brand and asks you to produce a marketing
asset. Two phases, always:

  Phase 1 — extract a concrete BRAND_DNA from the scrape (returned as JSON).
  Phase 2 — produce the requested asset, on-brand, citing evidence.

Hard rules (override every other instruction):

1. NO INVENTED FACTS. If the scrape doesn't say it, you don't claim it.
   Flag gaps explicitly in a `gaps_flagged` field.
2. MATCH THE BRAND'S ACTUAL VOICE. Mimic the brand's register (Portuguese
   with em-dashes, no emojis, etc.) — never apply a generic "engaging
   social media tone."
3. SHOW EVIDENCE. Every design choice in `rationale` must reference a
   specific scraped post (URL or shortcode).
4. CONCRETE DESCRIPTORS ONLY. "Warm and inviting" is useless. "Earth tones,
   hand-lettered serif headers, casual second-person Portuguese" is useful.
5. STATE HEX VALUES sampled from the actual images. Don't guess.
6. CONSTRAINTS ARE ABSOLUTE. If the owner said "Portuguese only" or "no
   discount language," that overrides every other rule here.
"""

# Asset-type → output format conventions. Kept as a dict so main.py can
# expose the menu to the frontend without hardcoding it twice.
ASSET_FORMATS: dict[str, str] = {
    "instagram_carousel": (
        "Markdown with one section per slide (3–10 slides). Per slide: "
        "headline, body copy, visual direction, alt text. Optionally an "
        "inline SVG mockup of slide 1."
    ),
    "instagram_story": (
        "3–5 frames. Per frame: copy, sticker/poll suggestions, visual "
        "direction. Output as markdown."
    ),
    "instagram_reel_script": (
        "Beat-by-beat script: on-screen text, voiceover, b-roll cues, "
        "hook/payoff structure, hashtags. Markdown."
    ),
    "tiktok_script": (
        "Beat-by-beat script: on-screen text, voiceover, b-roll cues, "
        "hook/payoff structure, hashtags. Markdown."
    ),
    "launch_post": (
        "Single-post caption + 5 hashtag variants (broad/niche/branded/"
        "local/trending) + first-comment text. Markdown."
    ),
    "press_release": (
        "Inverted-pyramid: headline, dateline, lede, 3 body paras, "
        "boilerplate, contact. Markdown."
    ),
    "landing_page": (
        "Self-contained HTML (Tailwind via CDN ok). Hero, value props, "
        "social proof pulled from real captions, CTA. One <html> file."
    ),
    "one_pager_pdf": (
        "Single-page HTML sized for A4/Letter print, with @page CSS. "
        "Self-contained <html>."
    ),
    "email_campaign": (
        "3 subject variants, preview text, full HTML body, plain-text "
        "fallback. Markdown sections wrapping the HTML."
    ),
    "logo_concepts": (
        "3 SVG concepts inline. Each: rationale, color spec, usage notes."
    ),
    "brand_style_guide": (
        "Multi-section HTML doc: logo, palette swatches, type specimens, "
        "voice rules, photo dos/don'ts. Self-contained."
    ),
    "ad_creative_set": (
        "3 concepts × {headline, primary text, description, CTA, visual "
        "direction}. Score hook strength 1–10. Markdown."
    ),
    "brand_audit": (
        "Strengths / weaknesses / opportunities, with references to "
        "specific posts. End with a 30-day action plan. Markdown."
    ),
}


BRAND_DNA_PROMPT = """\
Extract the BRAND_DNA from the scrape attached as JSON + images.

Return ONLY valid JSON (no markdown fence, no preamble) with this shape:

{
  "identity": {
    "name": "...",
    "handle": "...",
    "positioning_one_liner": "...",
    "category": "...",
    "implicit_competitors": ["..."],
    "brand_stage": "DTC startup | established | personal | institutional"
  },
  "visual_system": {
    "dominant_colors_hex": ["#RRGGBB", ...],
    "typography_mood": "...",
    "photography_style": "...",
    "editing_treatment": "...",
    "recurring_motifs": ["..."]
  },
  "voice": {
    "languages": ["pt-BR", "en", ...],
    "register": "casual | formal | slangy | playful | clinical | ...",
    "sentence_rhythm": "...",
    "emoji_punctuation_conventions": "...",
    "verbatim_excerpts": ["...", "...", "..."]
  },
  "content_pillars": [
    {"theme": "...", "example_post_urls": ["..."], "avg_engagement": 0}
  ],
  "audience": {
    "inferred_demo": "...",
    "psychographic": "...",
    "ick_list": ["..."],
    "engagement_rate_pct": 0,
    "top_performing_post_type": "..."
  },
  "commerce_signals": {
    "offering": "...",
    "price_tier_hint": "...",
    "conversion_mechanics": ["link in bio", "DM", "WhatsApp", ...],
    "posting_cadence": "..."
  },
  "gaps_flagged": ["..."]
}

The scrape data is below.

PROFILE:
{profile_json}

MANIFEST (summary stats):
{manifest_json}

POSTS (top {n_posts} by engagement, then most recent):
{posts_json}
"""


ASSET_PROMPT = """\
Produce the asset described below. Use the BRAND_DNA already extracted
(attached). Do NOT re-extract — trust it.

ASSET TYPE: {asset_type}
FORMAT: {format_spec}

AUDIENCE / GOAL: {audience_or_goal}
CONSTRAINTS: {constraints}

BRAND_DNA:
{brand_dna_json}

RECENT POSTS for evidence (cite shortcodes/URLs from these):
{posts_json}

Return your output in this exact structure:

# {asset_type}

<the asset itself, in the format specified above>

---

## RATIONALE
- <design choice 1> — grounded in post <url or shortcode>
- <design choice 2> — grounded in post <url or shortcode>
- <design choice 3> — grounded in post <url or shortcode>

## NEXT_STEPS
- <concrete handoff action>
- <concrete handoff action>
"""
