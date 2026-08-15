#!/usr/bin/env python3
"""pSEO generator — census.jsonl -> static site (rmtree +
rebuild + publish threshold). Zero deps, stdlib only.

  python3 site/generate.py [--base https://domain.tld]

Outputs site/dist/:
  index.html                 directory + hero (the free audience engine)
  api/{domain}/index.html    one record page per publishable API
  category/{slug}/index.html category roundups
  sitemap.xml, robots.txt, llms.txt, style.css
  data/sample.jsonl          free-sample funnel (the published records, verbatim)

Publish threshold: >=4 of the 8 term fields non-null (thin/null-heavy pages hurt
trust AND SEO). Every page: per-field evidence links, last_verified, honest nulls,
correction link, JSON-LD schema.org/WebAPI, answer-engine summary sentence.
Category is always normalized to a canonical bucket — the generator NEVER drops a
record for an empty bucket.

Sponsor layer (2026-07-12): site/sponsors.json drives paid placement —
  {"categories": {"<cat-slug>": {"name","url","tagline"}}, "featured": ["<domain>"]}
Category sponsor = one disclosed slot above the category table; featured domain =
pinned to the top of its category table with a FEATURED pill. Placement, never
data: sponsorship must never alter a record, field value, confidence, or listing.
"""
import argparse
import json
import re
import shutil
import time
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "data" / "census.jsonl"
CHANGELOG = ROOT / "data" / "changelog.jsonl"
DIST = ROOT / "site" / "dist"
# Baseline month the corpus was first fully verified — the "tracking since" anchor.
# Copy stays cadence-neutral ("re-verified on a schedule") until the weekly cron runs.
BASELINE_LABEL = "July 2026"
# Filled by main() before any page renders — drives the footer's operational line.
STATS = {"n": 0, "last": ""}
SIG_LABEL = {"pricing": "Pricing", "limits": "Rate limits", "auth": "Auth",
             "spec": "OpenAPI spec", "mcp": "MCP server", "info": "Details"}
# Every record gets a page (the site IS the dataset; "not documented" is data —
# site policy). Records under this many filled fields are rendered
# with <meta name=robots noindex> and kept out of the sitemap so thin pages can't
# hurt the domain's search quality; humans and agents still get every record.
INDEX_MIN_FIELDS = 4
FIELDS = ["base_url", "auth_type", "free_tier", "pricing_model", "pricing_details",
          "rate_limits", "openapi_spec_url", "mcp_server"]
FIELD_LABELS = {"base_url": "Base URL", "auth_type": "Auth", "free_tier": "Free tier",
                "pricing_model": "Pricing model", "pricing_details": "Pricing",
                "rate_limits": "Rate limits", "openapi_spec_url": "OpenAPI spec",
                "mcp_server": "MCP server"}
# Human display for enum-ish values — internal strings (api_key, usage_based) never
# render raw in the UI (review directive 2026-07-16); the raw value stays in the JSON.
HUMAN = {"api_key": "API key", "bearer_token": "Bearer token", "oauth2": "OAuth 2.0",
         "basic": "Basic auth", "none": "no auth", "usage_based": "usage based"}
GITHUB = "https://github.com/iljbrands-boop/apiterms-pipeline"


def hum(val):
    return HUMAN.get(val, str(val).replace("_", " ")) if val else val
# No email addresses anywhere on the site (site policy): corrections,
# claims and sponsor contact all go through Formspree forms (/correct/, /sponsors/).
SPONSORS = ROOT / "site" / "sponsors.json"


def load_sponsors():
    if SPONSORS.exists():
        s = json.loads(SPONSORS.read_text())
    else:
        s = {}
    return {"categories": s.get("categories", {}), "featured": set(s.get("featured", [])),
            "main": s.get("main"),   # main partner: {"name","url","tagline"} — homepage slot
            # Published rates. Edit sponsors.json, never this file — the whole point
            # of keeping them in data is that changing a price is not a code change.
            # Omit the "rates" key entirely and the pricing card renders nothing,
            # which is the correct behaviour when we don't want to quote publicly.
            "rates": s.get("rates")}


# site/config.json: third-party service IDs. Empty string = feature not injected.
#   crisp_website_id      -> Crisp chat bubble on every page
#   formspree_project_id  -> sponsor-inquiry form + change-feed email capture
#                            (form keys "sponsor"/"feed" live in ../formspree.json,
#                             deployed via `npx @formspree/cli deploy -k <deploy key>`)
CONFIG_PATH = ROOT / "site" / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
CRISP_ID = CONFIG.get("crisp_website_id", "")
FORMSPREE_PROJECT = CONFIG.get("formspree_project_id", "")

# No booking link. There was a "Book a call →" button on /sponsors/ and /dataset/
# pointing at cal.com/apiterms, which never existed and 404'd for every visitor who
# clicked it (removed 2026-08-12). Sponsor and licensing contact goes through the
# Formspree forms only — they deliver to Iron's inbox (see ../formspree.json). If a
# booking link is ever wanted, provision the account FIRST, then add it back here.

# Homepage social proof. IMPORTANT: these are PLACEHOLDERS — obviously-fake
# names/companies so nothing here can be mistaken for a real endorsement. The trust
# brand forbids fabricated quotes on the live site. Replace with REAL, permissioned
# quotes before shipping publicly; DO NOT SHIP PLACEHOLDERS PUBLICLY. When the list is
# empty the whole testimonials section renders nothing (safe to deploy with no fakes).
#   -> Go live: paste real entries below. Hide the section entirely: set TESTIMONIALS = []
# Empty = section is hidden (nothing renders). Paste REAL, permissioned quotes to go live.
# Format (one dict per quote):
#   {"quote": "...", "name": "Real Name", "role": "Title", "company": "Company"},
TESTIMONIALS = []  # hidden until a quote earns its place (removed 2026-07-16 — added no value)


def form_action(key):
    return f"https://formspree.io/p/{FORMSPREE_PROJECT}/f/{key}"

# ---------------------------------------------------------------- categories

CANON = [
    ("Crypto & Blockchain", ["cryptocurrency", "blockchain", "crypto", "defi", "web3"]),
    ("Payments", ["payment", "billing", "invoic"]),
    ("Finance", ["finance", "financial", "currency", "exchange", "banking", "stock", "trading"]),
    ("Developer Tools", ["developer", "development", "devtool", "continuous integration",
                         "ci/cd", "api documentation", "testing", "sdk", "git",
                         "test data", "mock", "url short", "webhook", "collaboration"]),
    ("Cloud & Infrastructure", ["cloud", "hosting", "infrastructure", "serverless", "iot"]),
    ("AI & Machine Learning", ["machine learning", "artificial intelligence", " ai", "ai ",
                               "llm", "speech", "text-to-speech", "nlp", "vision"]),
    ("Security & Auth", ["security", "authentication", "authorization", "auth", "identity",
                         "fraud", "malware", "antivirus", "threat", "privacy"]),
    ("Geo & Location", ["geocod", "location", "geospatial", "maps", "map ", "places"]),
    ("Data & Enrichment", ["open data", "data enrichment", "enrichment", "dataset", "scraping"]),
    ("Communication", ["sms", "email", "telecom", "messaging", "voice", "chat",
                       "communication", "notification"]),
    ("E-commerce", ["ecommerce", "e-commerce", "shopping", "retail", "product"]),
    ("Media & Content", ["media", "video", "music", "books", "anime", "movies", "photo",
                         "image", "text", "news", "content", "entertainment", "streaming",
                         "art", "design", "font"]),
    ("Productivity", ["productivity", "documents", "calendar", "notes", "tasks", "forms"]),
    ("Social", ["social"]),
    ("Jobs", ["jobs", "recruit", "hiring"]),
    ("Weather", ["weather", "climate", "meteo"]),
    # Transport is BEFORE Sports on purpose: "sport" is a substring of "transport",
    # and first-match-wins keeps transport records out of the Sports bucket.
    ("Transport & Travel", ["transport", "travel", "vehicle", "flight", "shipping",
                            "logistics", "aviation"]),
    ("Government", ["government", "civic", "public sector"]),
    ("Marketing", ["marketing", "seo", "analytics", "advertising", "tracking", "email marketing"]),
    ("Business & CRM", ["business", "crm", "sales", "customer relationship", "erp"]),
    ("Health & Science", ["health", "medical", "science", "genetic", "pharma",
                          "environment", "ecolog", "energy", "sustainab"]),
    # Long-tail buckets (mostly the public-apis hobby set) — appended last so they never
    # shadow a more specific match above. All keys are safe substrings of their labels.
    ("Games & Comics", ["game", "comic", "esports", "gaming"]),
    ("Sports & Fitness", ["sport", "fitness", "workout", "exercise", "athlet"]),
    ("Animals", ["animal"]),
    ("Food & Drink", ["food", "drink", "recipe", "restaurant", "beverage", "cocktail",
                      "grocery", "nutrition"]),
    ("Personality & Fun", ["personality", "horoscope", "astrolog", "tarot", "joke",
                           "meme", "trivia", "novelty"]),
]


def norm_category(raw) -> str:
    t = (raw or "").strip().lower().replace("_", " ")
    if not t:
        return "Other"
    for canon, keys in CANON:
        if any(k in t for k in keys):
            return canon
    return "Other"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- helpers

def v(rec, f):
    o = rec.get(f)
    return o.get("value") if isinstance(o, dict) else None


def ev(rec, f):
    o = rec.get(f)
    return o.get("evidence_url") if isinstance(o, dict) else None


def filled(rec) -> int:
    return sum(1 for f in FIELDS if v(rec, f) is not None)


# Curated, indexable collection pages (review §7: curated facets, never combinatorial)
COLLECTIONS = [
    ("free-apis", "Free APIs", "a documented free tier",
     lambda r: v(r, "free_tier")),
    ("no-auth-apis", "No-auth APIs", "no API key or auth required",
     lambda r: (v(r, "auth_type") or "") == "none"),
    ("openapi-apis", "APIs with OpenAPI specs", "a published OpenAPI/Swagger specification",
     lambda r: v(r, "openapi_spec_url")),
    ("mcp-apis", "APIs with MCP servers", "a documented Model Context Protocol server",
     lambda r: v(r, "mcp_server")),
]


def host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def logo(dom: str, big=False) -> str:
    """Vendor favicon via Google's s2 service (Clearbit logo API is dead — verified
    2026-07-13). onerror hides broken icons so rows degrade gracefully."""
    cls = "logo lg" if big else "logo"
    return (f'<img class="{cls}" loading="lazy" alt="" '
            f'src="https://www.google.com/s2/favicons?domain={dom}&amp;sz=64" '
            f'onerror="this.style.display=\'none\'">')


# ---------------------------------------------------------------- templates

CSS = """
:root{--void:#f7f8fa;--panel:#ffffff;--panel2:#f2f4f8;--line:#eaedf2;--lineh:#dce0e7;
--ink:#0c1424;--body:#4a5568;--dim:#8b95a3;--ghost:#aab2be;--blue:#1f5eff;--bluehot:#1a53d8;
--bluedim:#d7e3ff;--add:#00a368;--adddim:rgba(0,163,104,.10);--warn:#b8760a;
--shadow:0 1px 2px rgba(12,20,36,.04),0 4px 14px rgba(12,20,36,.05);
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--void);
color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--bluehot);text-decoration:none}a:hover{text-decoration:underline}
::selection{background:var(--blue);color:#fff}
.shell{max-width:1080px;margin:0 auto;padding:0 clamp(16px,4vw,40px) 60px}
.mono{font-family:var(--mono)}
.nav{display:flex;align-items:center;gap:18px;padding:14px 0;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-weight:700;font-size:14px;color:var(--ink);white-space:nowrap}
.brand .mark{color:#fff;background:var(--blue);padding:4px 8px;font-size:11.5px;border-radius:5px}
.topnav{margin-left:auto;display:flex;align-items:center;gap:16px;font-size:13px;font-weight:500;line-height:1}
.topnav a{white-space:nowrap}
@media(max-width:700px){.nav{flex-wrap:wrap}.topnav{margin-left:0;width:100%;overflow-x:auto;gap:14px;padding-bottom:4px;-webkit-overflow-scrolling:touch}}
.topnav a{color:var(--body)}
.topnav a.nav-add{color:var(--add);border:1px solid rgba(0,163,104,.35);padding:5px 10px;border-radius:6px;line-height:1}
.topnav a.nav-add:hover{background:var(--adddim);text-decoration:none}
.topnav a.nav-sp{color:var(--bluehot);border:1px solid var(--bluedim);background:rgba(31,94,255,.06);padding:5px 10px;border-radius:6px;line-height:1}
.topnav a.nav-sp:hover{background:rgba(31,94,255,.12);text-decoration:none}
.ghstar{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--lineh);background:var(--panel);
border-radius:6px;padding:4px 10px;color:var(--body)!important;font-size:12.5px;font-weight:600;white-space:nowrap;line-height:1}
.ghstar:hover{border-color:var(--ghost);text-decoration:none!important}
.ghstar svg{width:14px;height:14px;fill:var(--ink);flex:none}
.ghstar .cnt{border-left:1px solid var(--line);padding-left:8px;margin-left:2px;color:var(--ink);font-variant-numeric:tabular-nums}
.crumbs{font-family:var(--mono);font-size:11.5px;color:var(--dim);margin:20px 0;letter-spacing:.04em;text-transform:uppercase}
.crumbs a{color:var(--dim)}.crumbs span{color:var(--ghost);margin:0 8px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
h1{font-family:var(--sans);font-weight:750;letter-spacing:-.02em}
.kicker{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--bluehot);margin-bottom:10px}
.kicker:before{content:"// "}
.chip{font-size:12.5px;font-weight:500;padding:4px 11px;border:1px solid var(--lineh);color:var(--body);white-space:nowrap;display:inline-block;border-radius:6px}
.chip.cat{color:var(--bluehot);border-color:var(--bluedim);background:rgba(31,94,255,.08)}
.badge{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;letter-spacing:.05em;color:var(--add);background:var(--adddim);border:1px solid rgba(0,163,104,.35);padding:4px 11px;text-transform:uppercase;border-radius:6px}
.dot{width:6px;height:6px;background:var(--add)}
.conf{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--body)}
.conf .bars{display:inline-flex;gap:2px}.conf .bars i{width:4px;height:11px;background:var(--lineh)}
.conf.high .bars i{background:var(--add)}.conf.medium .bars i:nth-child(-n+2){background:var(--warn)}
.field{display:grid;grid-template-columns:150px minmax(0,1fr) auto;gap:16px;align-items:start;padding:14px 26px;border-top:1px solid var(--line)}
.field:hover{background:var(--panel2)}
.field .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);padding-top:3px}
.field .v{font-family:var(--mono);font-size:13px;color:var(--ink);line-height:1.55;word-break:break-word}
.field .v .hl{color:var(--bluehot);font-weight:600}
.field.absent .v{color:var(--ghost);font-style:italic}
.src{font-family:var(--mono);font-size:11px;color:var(--warn);white-space:nowrap;padding-top:3px}
.src.none{color:var(--ghost)}
.tag-null{display:inline-block;font-family:var(--mono);font-size:10.5px;color:var(--ghost);border:1px solid var(--line);padding:1px 7px;margin-left:4px;font-style:normal;border-radius:4px}
@media(max-width:560px){.field{grid-template-columns:minmax(0,1fr);gap:5px;padding:14px 18px}}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);text-align:left;font-weight:600;padding:11px 9px;border-bottom:1px solid var(--lineh);background:var(--void);white-space:nowrap}
td{padding:10px 9px;border-bottom:1px solid var(--line);font-family:var(--mono);color:var(--body);vertical-align:middle;white-space:nowrap}
td.name{max-width:230px;overflow:hidden;text-overflow:ellipsis}
td.name .dom{display:block;color:var(--ghost);font-size:11px;margin-top:2px;overflow:hidden;text-overflow:ellipsis}
th:first-child,td:first-child{padding-left:14px}th:last-child,td:last-child{padding-right:14px}
tr:last-child td{border-bottom:none}tr:hover td{background:var(--panel2)}
td.name a{font-weight:600;color:var(--ink)}
.pill{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border:1px solid var(--lineh);color:var(--body);border-radius:5px}
.pill.ok{color:var(--add);border-color:rgba(0,163,104,.35);background:var(--adddim)}
.pill.lo{color:var(--warn);border-color:rgba(184,118,10,.35)}
.yes{color:var(--add)}.no{color:var(--ghost)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);background:var(--panel);border-radius:12px;box-shadow:var(--shadow)}
.grid-stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:var(--panel);margin:26px 0;border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
@media(max-width:760px){.grid-stats{grid-template-columns:repeat(2,1fr)}}
.cell{padding:18px 20px;border-right:1px solid var(--line)}.cell:last-child{border-right:none}
.cell .n{font-family:var(--mono);font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
.cell .n em{font-style:normal;color:var(--bluehot)}
.cell .l{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:5px;letter-spacing:.08em;text-transform:uppercase}
.card{padding:20px;margin-bottom:22px}
.card h3{margin:0 0 14px;font-size:11px;font-family:var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--bluehot)}
.card h3:before{content:"// ";color:var(--dim)}
.card ul{margin:0;padding:0}
.card li{list-style:none;display:flex;gap:10px;margin-bottom:11px;font-size:13.5px;color:var(--body)}
.card li b{color:var(--ink)}.card .ck{color:var(--add);font-family:var(--mono);flex:none}
.btn{display:block;text-align:center;font-weight:600;font-size:13.5px;color:var(--ink);border:1px solid var(--lineh);padding:10px 12px;width:100%;border-radius:8px}
.btn:hover{border-color:var(--bluehot);color:var(--bluehot);text-decoration:none}
.code{font-family:var(--mono);font-size:12px;background:var(--void);border:1px solid var(--line);padding:12px 13px;color:var(--body);overflow-x:auto;line-height:1.7;border-radius:8px}
.code .c{color:var(--ghost)}.code .m{color:var(--bluehot)}.code .s{color:var(--add)}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:22px;align-items:start}
@media(max-width:900px){.cols{grid-template-columns:minmax(0,1fr)}}
.catlist{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 26px}
.shell:has(.home){max-width:1240px}
.home{display:grid;grid-template-columns:210px minmax(0,1fr);gap:34px;align-items:start;margin-top:18px}
.home-main{min-width:0}
.rail{position:sticky;top:14px;font-family:var(--mono);font-size:12px;max-height:calc(100vh - 26px);overflow-y:auto;padding-right:2px}
.rail-h{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--bluehot);margin:0 0 6px;padding:0 8px}
.rail-sec{margin-top:16px}
.rail-link{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:5px 8px;color:var(--body);border-radius:4px;line-height:1.35}
.rail-link:hover{background:var(--panel);color:var(--ink);text-decoration:none}
.rail-link.active{color:var(--ink);background:var(--panel);font-weight:600}
.rail-link span{color:var(--ghost);font-size:11px;font-variant-numeric:tabular-nums}
.rail-add{display:block;margin:18px 0 8px;text-align:center;color:var(--add);border:1px solid rgba(0,163,104,.3);background:var(--adddim);padding:8px;border-radius:5px;font-size:12px;letter-spacing:.02em}
.rail-add:hover{text-decoration:none;background:rgba(0,163,104,.16)}
.home-h1{font-family:var(--sans);font-size:clamp(22px,2.7vw,30px);line-height:1.2;letter-spacing:-.02em;margin:0 0 10px;max-width:20em;font-weight:750}
.home-sub{color:var(--body);font-size:14px;line-height:1.55;margin:0 0 15px;max-width:62ch}
.home-sub b{color:var(--ink)}
.statline{display:flex;gap:20px;flex-wrap:wrap;font-family:var(--mono);font-size:12px;color:var(--dim);margin:0 0 12px;padding-bottom:14px;border-bottom:1px solid var(--line)}
.statline b{color:var(--ink);font-size:14px;font-variant-numeric:tabular-nums}
.mobcats{display:none}
@media(max-width:820px){
  .home{grid-template-columns:1fr;gap:0}
  .rail{display:none}
  .mobcats{display:flex;margin:12px 0 2px}
}
.add{color:var(--add)}.del{color:var(--del,#c92a2a);text-decoration:line-through;text-decoration-thickness:1px}
.chip.sig{color:var(--warn);border-color:rgba(184,118,10,.3);background:rgba(184,118,10,.07)}
.chg-row{border-top:1px solid var(--line);padding:12px 0}
.chg-hd{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.chg-dom{font-family:var(--mono);font-weight:600;color:var(--ink)}
.chg-date{margin-left:auto;color:var(--dim);font-size:11.5px}
.chg-diff{font-size:12.5px;margin-top:6px;line-height:1.7;word-break:break-word}
.chg-diff .src{margin-left:8px}
.ticker{border:1px solid var(--line);background:var(--panel);border-radius:10px;box-shadow:var(--shadow);padding:12px 16px;margin:18px 0 0;font-family:var(--mono);font-size:12.5px;display:flex;align-items:center;gap:12px;overflow-x:auto;white-space:nowrap}
.ticker .lb{color:var(--add);flex:none;letter-spacing:.06em;text-transform:uppercase;font-size:10.5px}
.ticker a{color:var(--body)}.ticker .sep{color:var(--ghost)}
footer{margin-top:44px;padding-top:22px;border-top:1px solid var(--line);font-family:var(--mono);font-size:12px;color:var(--dim);letter-spacing:.03em;line-height:1.9}
footer a{color:var(--dim)}
.sub{color:var(--body);max-width:44em}
.fld{font-family:var(--mono);font-size:13px;color:var(--ink);background:var(--void);
border:1px solid var(--lineh);padding:10px 12px;width:100%;box-sizing:border-box;border-radius:8px}
.fld:focus{outline:none;border-color:var(--bluehot)}
textarea.fld{min-height:90px;resize:vertical}
.add-note{font-family:var(--mono);font-size:12.5px;line-height:1.5;padding:10px 12px;margin:0 0 12px;border:1px solid var(--lineh);border-radius:8px}
.add-note.ok{color:var(--add);border-color:rgba(0,163,104,.3);background:var(--adddim)}
.add-note.err{color:var(--warn);border-color:rgba(184,118,10,.3);background:rgba(184,118,10,.08)}
.btn.solid{background:var(--blue);border-color:var(--blue);color:#fff;cursor:pointer}
.btn.solid:hover{background:var(--bluehot);color:#fff;text-decoration:none}
.caprow{display:flex;gap:10px;margin-top:14px}
.caprow .fld{flex:1}.caprow .btn{width:auto;padding:10px 18px}
@media(max-width:560px){.caprow{flex-direction:column}}
.logo{width:18px;height:18px;object-fit:contain;vertical-align:-4px;margin-right:9px;background:#fff;border-radius:3px;flex:none}
.logo.lg{width:30px;height:30px;vertical-align:-6px;border-radius:5px}
.searchwrap{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:20px 0 14px}
.search{flex:1;min-width:240px;background:var(--panel);border:1px solid var(--lineh);color:var(--ink);
font-family:var(--mono);font-size:13.5px;padding:12px 15px;outline:none;border-radius:9px;box-shadow:var(--shadow)}
.search:focus{border-color:var(--blue);box-shadow:0 0 0 3px var(--bluedim)}
.search::placeholder{color:var(--ghost)}
.kbd{font-family:var(--mono);font-size:10px;color:var(--dim);border:1px solid var(--line);padding:2px 6px;border-radius:4px}
.fchip{cursor:pointer;user-select:none}
.fchip.on{color:var(--bluehot);border-color:var(--bluehot);background:rgba(31,94,255,.12)}
.nshow{font-family:var(--mono);font-size:11px;color:var(--dim)}
.sponsorbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 16px;margin:18px 0 0;
font-family:var(--mono);font-size:12.5px;color:var(--body)}
.sponsorbar a b{color:var(--ink)}
.sponsorbar.open{border-style:dashed;color:var(--dim)}
.pill.sp{color:var(--warn);border-color:rgba(184,118,10,.35);flex:none}
.pill.feat{color:var(--bluehot);border-color:var(--bluedim);background:rgba(31,94,255,.08)}
.quotes{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:14px 0 8px}
@media(max-width:760px){.quotes{grid-template-columns:1fr}}
.quote{position:relative;padding:22px 20px 18px;display:flex;flex-direction:column;gap:14px}
.quote p{margin:0;font-size:14px;line-height:1.6;color:var(--ink)}
.quote p:before{content:"“";color:var(--blue);font-family:var(--mono);font-size:20px;margin-right:2px}
.quote .who{margin-top:auto;font-family:var(--mono);font-size:11.5px;line-height:1.5;color:var(--dim)}
.quote .who b{display:block;color:var(--body);font-size:12px;letter-spacing:.02em}
.btn.inline{display:inline-block;width:auto;padding:11px 20px}
.cta-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:18px 0 10px}
.cta-sub{font-family:var(--mono);font-size:12px;color:var(--dim);margin:0 0 4px}
.cta-sub a{color:var(--body)}
.trust{font-family:var(--mono);font-size:11.5px;color:var(--dim);margin:10px 0 0;letter-spacing:.02em}
.trust b{color:var(--body)}
td .nd{color:var(--ghost);font-size:10px;font-style:normal;letter-spacing:.04em}
td .okv{color:var(--add)}
td.dt{color:var(--dim);font-size:11px;white-space:nowrap}
.fchip.chgc{color:var(--warn);border-color:rgba(184,118,10,.4);background:rgba(184,118,10,.07)}
.fchip.chgc.on{color:#fff;background:var(--warn);border-color:var(--warn)}
.sect{margin:34px 0 0}
.sect h2{font-size:19px;letter-spacing:-.01em;margin:6px 0 10px;font-weight:700}
.sect p{color:var(--body);font-size:14px;max-width:66ch;margin:0 0 12px}
.chg-panel{padding:6px 20px 14px;margin-top:26px}
.chg-panel .chg-row:first-of-type{border-top:none}
.chg-empty{padding:18px 0 8px;font-size:13.5px;color:var(--body);max-width:60ch}
.chg-empty .lb{display:block;font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--add);margin-bottom:8px}
#apitable.collapsed .xtra{display:none}
.showall{display:block;width:100%;text-align:center;font-size:13px;font-weight:600;color:var(--bluehot);
background:var(--panel);border:none;border-top:1px solid var(--line);padding:13px;cursor:pointer;font-family:var(--sans)}
.showall:hover{background:var(--panel2)}
.statmini{font-family:var(--mono);font-size:12.5px;color:var(--dim);margin:14px 0 0}
.statmini b{color:var(--ink);font-variant-numeric:tabular-nums}
.findrow{display:flex;gap:26px;flex-wrap:wrap;font-size:14px;color:var(--body);margin:10px 0 12px}
.findrow b{font-family:var(--mono);font-size:20px;color:var(--ink);display:block;font-variant-numeric:tabular-nums}
.linklike{background:none;border:none;padding:4px 2px;color:var(--body);font-size:13.5px;font-weight:600;cursor:pointer;font-family:var(--sans)}
.linklike:hover{color:var(--ink)}
.seclabel{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
.why2col{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:32px;align-items:start}
@media(max-width:820px){.why2col{grid-template-columns:1fr}}
.fieldcard{padding:16px 18px}
.fieldcard .fc-h{font-size:13px;font-weight:700;color:var(--ink);margin-bottom:10px}
.fieldcard .fc-r{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;padding:7px 0;border-top:1px solid var(--line);font-family:var(--mono);font-size:12.5px}
.fieldcard .fc-r .k{color:var(--dim);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;padding-top:2px}
.fieldcard .fc-r .v{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.osscard{padding:22px 24px;margin-top:38px}
.osscard h2{font-size:17px;margin:4px 0 8px}
.osscard p{color:var(--body);font-size:13.5px;max-width:60ch;margin:0 0 12px}
.oss-facts{display:flex;gap:20px;flex-wrap:wrap;font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.foot-cols{display:flex;justify-content:space-between;gap:28px;flex-wrap:wrap}
.foot-cols .fc-l b{color:var(--body)}
.foot-cols .fc-r{text-align:right}
@media(max-width:640px){.foot-cols .fc-r{text-align:left}}
.foot-meta{margin-top:16px;padding-top:10px;border-top:1px solid var(--line);color:var(--ghost);font-size:11.5px}
.foot-meta a{color:var(--ghost)}
"""


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" fill="#1f5eff"/>
<text x="32" y="34" text-anchor="middle" dominant-baseline="central"
 font-family="ui-monospace,'SF Mono',Menlo,Consolas,monospace" font-weight="700"
 font-size="38" fill="#fff">/A</text>
</svg>
"""


# chat:show forces the bubble even when no operator is online (the Crisp dashboard's
# hide-when-unavailable behavior was hiding it — diagnosed live 2026-07-16); offline
# messages still reach the inbox + email.
CRISP_SNIPPET = ('<script>window.$crisp=[];window.CRISP_WEBSITE_ID="%s";'
                 'window.$crisp.push(["do","chat:show"]);'
                 '(function(){var d=document,s=d.createElement("script");'
                 's.src="https://client.crisp.chat/l.js";s.async=1;'
                 'd.getElementsByTagName("head")[0].appendChild(s);})();</script>')

# GitHub star badge in the nav — octocat mark + live star count. Count comes from the
# public GitHub API client-side (unauthenticated, per-visitor: well inside rate limits),
# cached in localStorage for an hour, and hidden entirely at 0 or on fetch failure —
# the badge never shows a fake or stale-zero number.
GH_MARK = ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 '
           '3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53'
           '-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 '
           '1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '
           '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 '
           '2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 '
           '1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 '
           '2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>')
# Formspree forms submit via fetch and confirm inline — visitors NEVER get bounced to
# Formspree's hosted thank-you page (same UX as /add/).
# Per-form success copy via data-ok on the <form>.
FORMS_JS = """<script>(function(){
[].forEach.call(document.querySelectorAll('form[action*="formspree.io"]'),function(f){
  f.addEventListener("submit",function(ev){
    ev.preventDefault();
    var btn=f.querySelector('[type=submit]'),
        box=f.parentNode.querySelector(".add-note");
    if(!box){box=document.createElement("div");box.className="add-note";box.setAttribute("role","status");
      f.parentNode.insertBefore(box,f);}
    if(btn)btn.disabled=true;
    fetch(f.action,{method:"POST",body:new FormData(f),headers:{Accept:"application/json"}})
    .then(function(r){if(!r.ok)throw 0;
      box.textContent=f.dataset.ok||"Thanks — sent. We read every message and reply by email.";
      box.className="add-note ok";f.style.display="none";})
    .catch(function(){if(btn)btn.disabled=false;
      box.textContent="Couldn't send just now — please try again in a moment.";
      box.className="add-note err";});
  });
});})();</script>"""

GH_STAR_JS = """<script>(function(){var e=document.getElementById("ghcnt");if(!e)return;
function show(n){if(n>0){e.textContent=n>=1000?(n/1000).toFixed(1).replace(/\\.0$/,"")+"k":n;e.hidden=false}}
var c=null;try{c=JSON.parse(localStorage.ghstars||"null")}catch(_){}
if(c&&Date.now()-c.t<36e5){show(c.n);return}
fetch("https://api.github.com/repos/iljbrands-boop/apiterms-pipeline")
.then(function(r){return r.json()}).then(function(d){var n=d.stargazers_count||0;
try{localStorage.ghstars=JSON.stringify({n:n,t:Date.now()})}catch(_){}show(n)})
.catch(function(){});})();</script>"""


def page(title, desc, canonical, body_html, base, jsonld=None, noindex=False):
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    crisp = CRISP_SNIPPET % CRISP_ID if CRISP_ID else ""
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(desc)}">
<link rel="canonical" href="{canonical}">
{robots}
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/style.css">
{ld}
</head>
<body>
<div class="shell">
<div class="nav">
  <a class="brand" href="/"><span class="mark">/API</span>&nbsp;TERMS</a>
  <nav class="topnav"><a href="/#browse">Browse</a><a href="/changes/">Changes</a><a href="/report/">Findings</a><a href="/dataset/">Dataset</a><a href="/methodology/">Methodology</a><a class="ghstar" href="{GITHUB}" target="_blank" rel="noopener" title="Star the open-source pipeline on GitHub">{GH_MARK}Star<span class="cnt" id="ghcnt" hidden></span></a><a href="/add/" class="nav-add">+ Add an API</a><a href="/sponsors/" class="nav-sp">Sponsor</a></nav>
</div>
{body_html}
<footer>
<div class="foot-cols">
  <div class="fc-l"><b>API Terms</b><br>The sourced record of public API terms and how they change.</div>
  <div class="fc-r"><a href="/#browse">Browse</a> · <a href="/changes/">Changes</a> · <a href="/report/">Findings</a> · <a href="/dataset/">Dataset</a> · <a href="/methodology/">Methodology</a><br>
  <a href="{GITHUB}" target="_blank" rel="noopener">Open source</a> · <a href="/add/">Add an API</a> · <a href="/correct/">Report an error</a> · <a href="/sponsors/">Sponsor</a></div>
</div>
<div class="foot-meta">{STATS['n']:,} APIs tracked · Source pages swept {STATS['last']} · records re-extracted when their terms change · <a href="/llms.txt">llms.txt</a></div>
</footer>
</div>
<script async src="https://scripts.simpleanalyticscdn.com/latest.js"></script>
{GH_STAR_JS}
{FORMS_JS}
{crisp}
</body>
</html>"""


def field_row(rec, f):
    label = FIELD_LABELS[f]
    val, src = v(rec, f), ev(rec, f)
    if val is None:
        return (f'<div class="field absent"><div class="k">{label}</div>'
                f'<div class="v">not documented <span class="tag-null">null</span></div>'
                f'<span class="src none">no source</span></div>')
    hl = ' class="hl"' if f in ("base_url", "auth_type", "pricing_model") else ""
    disp = hum(val) if f in ("auth_type", "pricing_model") else str(val)
    src_html = (f'<a class="src" href="{escape(src)}" rel="nofollow">{escape(host(src))} ↗</a>'
                if src else '<span class="src none">unevidenced</span>')
    return (f'<div class="field"><div class="k">{label}</div>'
            f'<div class="v"><span{hl}>{escape(disp)}</span></div>{src_html}</div>')


def summary_sentence(rec):
    """Answer-engine phrasing: the sentence an LLM should quote."""
    name = rec.get("name") or rec["domain"]
    bits = []
    if v(rec, "auth_type") == "none":
        bits.append("requires no authentication")
    elif v(rec, "auth_type"):
        bits.append(f"uses {hum(v(rec, 'auth_type'))} authentication")
    if v(rec, "pricing_model"):
        bits.append(f"has {hum(v(rec, 'pricing_model'))} pricing")
    if v(rec, "free_tier"):
        bits.append(f"offers a free tier ({v(rec, 'free_tier')})")
    if v(rec, "rate_limits"):
        bits.append(f"rate limits: {v(rec, 'rate_limits')}")
    tail = "; ".join(bits) if bits else "terms not fully documented by the vendor"
    return f"The {name} API {tail}. Verified {rec.get('last_verified')} with per-field source links."


def display_name(rec):
    """'Stripe API' stays; 'Clarifai' -> 'Clarifai API'. Never 'X API API'."""
    n = (rec.get("name") or rec["domain"]).strip()
    return n if n.lower().endswith("api") else n + " API"


def clip_value(text, limit):
    """Shorten a field value to snippet size without cutting mid-word or leaving
    dangling punctuation. Vendor values are often "600 req/min per token,
    increasable on request" — the first clause carries the number, which is the
    part a searcher is scanning for, so prefer it over a truncated full value."""
    t = " ".join(str(text).split())
    for sep in ("; ", " — ", ". ", ", "):
        head = t.split(sep)[0]
        if sep in t and 12 <= len(head):
            t = head
            break
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0]
    if t.count("(") > t.count(")"):          # never leave an unclosed bracket
        t = t.split("(")[0]
    return t.strip().rstrip(",;:—- ")


def meta_desc(rec):
    """Complete sentence from the strongest available facts; never mid-word truncation.
    Only mentions fields the record actually has.

    States the VALUES, not their existence. This used to say "a free tier and
    documented rate limits", which announces that we hold the fact rather than
    giving it — so a searcher on "notion api rate limit free tier" saw a page that
    promised the number without showing it. Those pages sat at position 5-10 with a
    0.50% CTR against a 3-5% norm (GSC, 33 days from 2026-07-11). The numbers ARE
    the product; put them in the snippet.

    Ordering matters too: the old version appended rate limits LAST and dropped
    from the end to fit, so the single most-searched fact was the first to go.
    Concrete values now lead and presence-only flags trail.
    """
    name = display_name(rec)
    facts = []
    if v(rec, "free_tier"):
        facts.append(f"free tier: {clip_value(v(rec, 'free_tier'), 58)}")
    if v(rec, "rate_limits"):
        facts.append(f"rate limits: {clip_value(v(rec, 'rate_limits'), 58)}")
    if v(rec, "pricing_model"):
        facts.append(f"{v(rec, 'pricing_model').replace('_', ' ')} pricing")
    if v(rec, "auth_type"):
        facts.append(f"{v(rec, 'auth_type').replace('_', ' ')} auth")
    if v(rec, "openapi_spec_url"):
        facts.append("OpenAPI spec")
    if v(rec, "mcp_server"):
        facts.append("MCP server")
    # Compact tail: every character it costs is a character not spent on a number.
    tail = f" Verified {rec.get('last_verified')}, source-linked."
    if not facts:
        # Length-guarded like every other path: this branch used to be returned
        # unchecked, so the 616 zero-field records with a long display name (e.g.
        # "UserCheck (formerly MailCheck.ai) API") overflowed 158 chars and got
        # cut mid-word by the search engine instead of by us.
        d = f"{name}: the vendor documents none of the terms we track.{tail}"
        return d if len(d) <= 158 else f"{name}: terms not documented by the vendor.{tail}"[:158]

    # Pack greedily rather than dropping from the end. Dropping meant one long
    # rate-limit value evicted the short pricing and auth facts behind it, leaving
    # descriptions at half the available width; and a single over-long fact could
    # not be shed at all, so 37 records still overflowed 158 chars.
    def assemble(items):
        return f"{name}: " + "; ".join(items) + f".{tail}"

    chosen = []
    for f in facts:
        if len(assemble(chosen + [f])) <= 158:
            chosen.append(f)
    if not chosen:
        # not even the strongest fact fits — clip it to whatever room is left
        room = 158 - len(assemble([""]))
        if room >= 20:
            chosen = [clip_value(facts[0], room)]
        else:
            return f"{name}: structured, source-linked API terms.{tail}"[:158]
    return assemble(chosen)


def record_page(rec, base, history=None):
    dom = rec["domain"]
    name = rec.get("name") or dom
    cat = rec["_category"]
    cat_slug = slugify(cat)
    conf = rec.get("confidence", "low")
    url = f"{base}/api/{dom}/"
    rows = "\n".join(field_row(rec, f) for f in FIELDS)
    title = f"{display_name(rec)}: pricing, auth, rate limits | API Terms"
    desc = meta_desc(rec)
    jsonld = {
        "@context": "https://schema.org", "@type": "WebAPI",
        "name": f"{name} API", "url": f"https://{dom}",
        "description": rec.get("what_it_does", ""),
        "documentation": (rec.get("evidence_pages") or [f"https://{dom}"])[0],
        "provider": {"@type": "Organization", "name": name},
        "dateModified": rec.get("last_verified"),
    }
    history_card = ""
    if history:
        rows = "\n".join(event_line(e, base) for e in
                         sorted(history, key=lambda e: e["detected"], reverse=True)[:8])
        history_card = f"""<div class="panel card">
    <h3>History</h3>
    <p class="sub" style="margin:0 0 4px;font-size:12.5px">What changed since we started tracking this API.</p>
    {rows}
  </div>"""
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span><a href="/category/{cat_slug}/">{escape(cat)}</a><span>/</span>{escape(name)}</div>
<div class="cols">
<main class="panel">
  <div style="padding:24px 26px 20px">
    <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">
      <h1 style="margin:0;font-size:28px">{logo(dom, big=True)}{escape(name)}</h1>
      <span class="mono" style="font-size:13px;color:var(--dim)">{escape(dom)}</span>
    </div>
    <p class="sub" style="margin:12px 0 6px;font-size:14.5px">{escape(rec.get('what_it_does') or '')}</p>
    <p class="sub" style="margin:0 0 18px;font-size:13px;color:var(--dim)">{escape(summary_sentence(rec))}</p>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <a class="chip cat" href="/category/{cat_slug}/">{escape(cat)}</a>
      <span class="badge"><span class="dot"></span>Verified {escape(rec.get('last_verified') or '')}</span>
      <span class="conf {conf}"><span class="bars"><i></i><i></i><i></i></span> {conf} confidence</span>
    </div>
  </div>
  <div style="border-top:1px solid var(--line)">
    {rows}
  </div>
</main>
<aside>
  <div class="panel card">
    <h3>Why trust this</h3>
    <ul>
      <li><span class="ck">▸</span><span>Every field links to <b>the exact page that states it</b>.</span></li>
      <li><span class="ck">▸</span><span>Re-checked on a schedule — last verified <b>{escape(rec.get('last_verified') or '')}</b>.</span></li>
      <li><span class="ck">▸</span><span>Honest nulls: <b>"not documented" is data</b>, never a guess.</span></li>
    </ul>
    <a class="btn" href="/correct/?domain={escape(dom)}&amp;kind=correction">Suggest a correction</a>
  </div>
  <div class="panel card" style="border-color:var(--bluedim);background:linear-gradient(180deg,rgba(31,94,255,.05),transparent)">
    <h3>Run {escape(name)}?</h3>
    <p class="sub" style="margin:0 0 12px;font-size:13px">Claim this page to keep your listing
    accurate and get ahead of changes. Claiming lets you:</p>
    <ul>
      <li><span class="ck">▸</span><span><b>Verify the record</b> — confirm every field is right, straight from the source.</span></li>
      <li><span class="ck">▸</span><span><b>Get change alerts</b> — know the moment we detect a pricing, limit or auth change.</span></li>
      <li><span class="ck">▸</span><span><b>Earn a verified badge</b> — and the option to feature your listing.</span></li>
    </ul>
    <a class="btn solid" href="/correct/?domain={escape(dom)}&amp;kind=claim">Claim this page →</a>
    <p class="sub" style="margin:10px 0 0;font-size:11.5px;color:var(--dim)">Free to claim. Placement never changes a field or its evidence.</p>
  </div>
  <div class="panel card">
    <h3>Machine-readable</h3>
    <div class="code"><span class="c"># this record as JSON</span>
<span class="m">GET</span> {base}/api/{escape(dom)}/record.json</div>
  </div>
  {history_card}
</aside>
</div>"""
    return page(title, desc, url, body, base, jsonld,
                noindex=filled(rec) < INDEX_MIN_FIELDS), url


def tri(val):
    """Tri-state cell: documented -> ✓, else an explicit 'n/d' (not documented).
    'n/d' is a finding, not an error — it must not look like a broken cell."""
    return '<span class="okv">✓</span>' if val else '<span class="nd" title="not documented">n/d</span>'


def table_rows(recs, base, featured=frozenset(), logo_limit=None, changed=frozenset(),
               xtra_after=None):
    """logo_limit caps how many rows get a favicon <img> — 1,250 external images
    on one page wedges slow renderers (observed in preview, 2026-07-13).
    changed = domains with a recent change-ledger event (drives the 'changed
    recently' filter chip on the homepage). xtra_after=N tags rows past N with
    class=xtra so the homepage can collapse the table to a first block; search
    and filters auto-expand."""
    out = []
    for i, r in enumerate(recs):
        cls = ' class="xtra"' if (xtra_after is not None and i >= xtra_after) else ""
        feat = ' <span class="pill feat">featured</span>' if r["domain"] in featured else ""
        s = " ".join(filter(None, [r.get("name"), r["domain"], r["_category"],
                                   hum(v(r, "auth_type")), hum(v(r, "pricing_model"))])).lower()
        flags = (f' data-s="{escape(s)}" data-free="{1 if v(r, "free_tier") else 0}"'
                 f' data-noauth="{1 if v(r, "auth_type") == "none" else 0}"'
                 f' data-rl="{1 if v(r, "rate_limits") else 0}"'
                 f' data-mcp="{1 if v(r, "mcp_server") else 0}"'
                 f' data-spec="{1 if v(r, "openapi_spec_url") else 0}"'
                 f' data-chg="{1 if r["domain"] in changed else 0}"'
                 f' data-hi="{1 if r.get("confidence") == "high" else 0}"')
        lg = logo(r["domain"]) if (logo_limit is None or i < logo_limit) else ""
        out.append(
            f'<tr{cls}{flags}><td class="name">{lg}<a href="/api/{r["domain"]}/">{escape(r.get("name") or r["domain"])}</a>{feat}'
            f'<span class="dom">{escape(r["domain"])}</span></td>'
            f'<td>{escape(r["_category"])}</td>'
            f'<td>{escape(hum(v(r, "auth_type")) or "")}{"" if v(r, "auth_type") else tri(None)}</td>'
            f'<td>{escape(hum(v(r, "pricing_model")) or "")}{"" if v(r, "pricing_model") else tri(None)}</td>'
            f'<td>{tri(v(r, "free_tier"))}</td>'
            f'<td>{tri(v(r, "rate_limits"))}</td>'
            f'<td>{tri(v(r, "openapi_spec_url"))}</td>'
            f'<td>{tri(v(r, "mcp_server"))}</td>'
            f'<td class="dt">{escape(r.get("last_verified") or "")}</td></tr>')
    return "\n".join(out)


TABLE_HEAD = ('<thead><tr><th>API</th><th>Category</th><th>Auth</th><th>Pricing</th>'
              '<th>Free tier</th><th>Limits</th><th>OpenAPI</th><th>MCP</th>'
              '<th>Checked</th></tr></thead>')


def sponsor_bar(cat, slug, sponsors):
    """Disclosed category-sponsor slot. Filled from sponsors.json, else an open-slot
    CTA. Placement only — never touches the table or the records."""
    sp = sponsors["categories"].get(slug)
    if sp:
        return (f'<div class="panel sponsorbar"><span class="pill sp">Sponsor</span>'
                f'<span><a href="{escape(sp["url"])}" rel="sponsored"><b>{escape(sp["name"])}</b></a>'
                f' — {escape(sp.get("tagline") or "")}</span></div>')
    return (f'<div class="panel sponsorbar open"><span class="pill sp">Slot open</span>'
            f'<span>Put your product in front of developers comparing {escape(cat)} APIs. '
            f'<a href="/sponsors/">Become the category sponsor →</a></span></div>')


def category_page(cat, recs, base, sponsors):
    slug = slugify(cat)
    url = f"{base}/category/{slug}/"
    n = len(recs)
    with_free = sum(1 for r in recs if v(r, "free_tier"))
    with_mcp = sum(1 for r in recs if v(r, "mcp_server"))
    featured = sponsors["featured"]
    ordered = sorted(recs, key=lambda r: r["domain"] not in featured)  # stable: pinned first
    title = f"{cat} APIs: auth, pricing, rate limits ({n} verified) — API Terms"
    desc = (f"{n} {cat} APIs with verified auth, pricing and rate limits. "
            f"{with_free} offer a free tier; {with_mcp} document an MCP server.")
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>{escape(cat)}</div>
<div class="kicker">Category</div>
<h1 style="font-size:26px;margin:0 0 8px">{escape(cat)} APIs</h1>
<p class="sub">{escape(desc)} Every field carries the source URL that states it.</p>
{sponsor_bar(cat, slug, sponsors)}
<div class="table-wrap" style="margin-top:18px"><table>{TABLE_HEAD}<tbody>
{table_rows(ordered, base, featured)}
</tbody></table></div>"""
    return page(title, desc, url, body, base), url


def testimonials_section():
    """Renders the homepage social-proof band — or nothing at all when TESTIMONIALS
    is empty. See the TESTIMONIALS constant: entries are placeholders and must be
    replaced with real, permissioned quotes before shipping publicly."""
    if not TESTIMONIALS:
        return ""
    cards = "\n".join(
        f'<figure class="panel quote">'
        f'<p>{escape(t["quote"])}</p>'
        f'<figcaption class="who"><b>{escape(t["name"])}</b>'
        f'{escape(t["role"])} · {escape(t["company"])}</figcaption></figure>'
        for t in TESTIMONIALS)
    return f"""<div class="kicker" style="margin-top:32px">What builders say</div>
<div class="quotes">{cards}</div>
"""


def index_page(recs, cats, base, corpus_stats, changelog=None, sponsors=None,
               pages_monitored=0, changed_recent=frozenset()):
    n = len(recs)
    sponsors = sponsors or {"main": None}
    # Stat band: all four over the SAME denominator (the published records) so the
    # numbers are internally consistent — no "tracked vs published" mismatch on the
    # homepage. The 297 bot-walled/JS-only domains live on /methodology as a
    # transparency line, not as a competing headline number.
    free_pct = round(100 * sum(1 for r in recs if v(r, "free_tier")) / max(n, 1))
    mcp_pct = round(100 * sum(1 for r in recs if v(r, "mcp_server")) / max(n, 1))
    rec_spec_pct = round(100 * sum(1 for r in recs if v(r, "openapi_spec_url")) / max(n, 1))
    mcp_n = sum(1 for r in recs if v(r, "mcp_server"))
    spec_n = sum(1 for r in recs if v(r, "openapi_spec_url"))
    ratio = round(mcp_n / spec_n, 1) if spec_n else 0
    last_check = max((r.get("last_verified") or "" for r in recs), default="")
    # ONE change-tracking panel: real ledger events + explanation + RSS + email capture.
    # Title switches from "How change tracking works" to "Recent API-term changes" the
    # moment real events exist. NEVER example/fabricated changes (evidence rule).
    evs = sorted(changelog or [], key=lambda e: e["detected"], reverse=True)[:3]
    if evs:
        chg_title = "Recent API-term changes"
        chg_events = "\n".join(event_line(e, base) for e in evs)
        chg_expl = ""
    else:
        chg_title = "How change tracking works"
        chg_events = ""
        chg_expl = (f'<span class="lb" style="display:block;font-family:var(--mono);font-size:10.5px;'
                    f'letter-spacing:.1em;text-transform:uppercase;color:var(--add);margin:12px 0 8px">'
                    f'● Tracking since {BASELINE_LABEL}</span>')
    chg_panel = f"""<div class="panel chg-panel" id="feed">
  <h3 style="margin:16px 0 2px;font-size:15px;font-weight:700;color:var(--ink)">{chg_title}</h3>
  {chg_events}
  {chg_expl}
  <p class="sub" style="margin:10px 0 0;font-size:13.5px">Every record's source pages are re-checked
  on a schedule. When a vendor moves a price, drops a free tier, tightens a rate limit or adds an
  MCP server, the diff lands here — old value, new value, source.
  <a href="/changes/">View all changes →</a> · <a href="/changes.xml">RSS</a></p>
  <div id="feed-msg" role="status"></div>
  <form class="caprow" action="/.netlify/functions/subscribe" method="POST" style="margin:12px 0 8px">
    <input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"
     style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">
    <input class="fld" type="email" name="email" placeholder="you@company.com" required>
    <button class="btn solid" type="submit">Join the email feed</button>
  </form>
</div>"""
    main_sp = sponsors.get("main")
    if main_sp:
        sponsor_unit = (f'<div class="panel sponsorbar" style="margin:18px 0 0">'
                        f'<span class="pill sp">Supported by</span>'
                        f'<span><a href="{escape(main_sp["url"])}" rel="sponsored"><b>{escape(main_sp["name"])}</b></a>'
                        f' — {escape(main_sp.get("tagline") or "")}</span></div>')
    else:
        sponsor_unit = ('<div class="panel sponsorbar open" style="margin:18px 0 0">'
                        '<span class="pill sp">Sponsor</span>'
                        '<span>Reach developers evaluating APIs. Sponsorship is clearly labelled '
                        'and never influences the dataset. <a href="/sponsors/">Sponsor API Terms →</a></span></div>')
    chg_chip = ('<span class="chip fchip chgc mf" data-f="chg" hidden>changed recently</span>'
                if changed_recent else "")
    title = "API Terms — public API terms, tracked over time"
    desc = (f"Authentication, pricing, free tiers, rate limits, OpenAPI and MCP for {n:,} "
            "public APIs. Sourced from vendor documentation and checked for changes.")
    free_n = sum(1 for r in recs if v(r, "free_tier"))
    noauth_n = sum(1 for r in recs if v(r, "auth_type") == "none")
    rail_cats = "\n".join(
        f'<a class="rail-link" href="/category/{slugify(c)}/">{escape(c)}<span>{len(rs):,}</span></a>'
        for c, rs in cats[:10])
    try:
        import datetime
        lc = datetime.date.fromisoformat(last_check)
        last_check_h = lc.strftime("%b %-d, %Y")
    except Exception:
        last_check_h = last_check
    # Homepage table order: most recently verified first (freshness is the product;
    # it also naturally surfaces records with visible n/d states, not just the 8/8
    # rows the completeness sort put on top). Stable sort keeps domain order in ties.
    recs_tbl = sorted(recs, key=lambda r: r["domain"])
    recs_tbl = sorted(recs_tbl, key=lambda r: (r.get("last_verified") or "", filled(r)),
                      reverse=True)
    # "Every field includes" illustration — built from a REAL record at build time
    # (never a mocked value; the caption names the record so readers can check it).
    ex = next((r for r in recs_tbl if v(r, "pricing_model")
               and "pricing" in (ev(r, "pricing_model") or "")), None) \
        or next((r for r in recs_tbl if v(r, "pricing_model") and ev(r, "pricing_model")), None)
    fieldcard = ""
    if ex:
        nhist = sum(1 for e in (changelog or []) if e["domain"] == ex["domain"])
        hist_txt = (f"{nhist} change{'s' if nhist != 1 else ''}" if nhist
                    else f"tracked since {BASELINE_LABEL}")
        src = ev(ex, "pricing_model")
        src_disp = re.sub(r"^https?://(www\.)?", "", src).rstrip("/")
        fieldcard = f"""<div class="panel fieldcard">
      <div class="fc-h">Every field includes</div>
      <div class="fc-r"><span class="k">Value</span><span class="v">{escape(hum(v(ex, "pricing_model")))}</span></div>
      <div class="fc-r"><span class="k">Source</span><span class="v"><a href="{escape(src)}" rel="nofollow">{escape(src_disp)} ↗</a></span></div>
      <div class="fc-r"><span class="k">Last verified</span><span class="v">{escape(ex.get("last_verified") or "")}</span></div>
      <div class="fc-r"><span class="k">History</span><span class="v">{escape(hist_txt)}</span></div>
      <p style="margin:10px 0 0;font-size:11.5px;color:var(--dim)">The live pricing field of
      <a href="/api/{escape(ex["domain"])}/">{escape(ex.get("name") or ex["domain"])}</a> — not a mock-up.</p>
    </div>"""
    mob_cats = "".join(
        f'<a class="chip cat" href="/category/{slugify(c)}/">{escape(c)} · {len(rs)}</a>'
        for c, rs in cats[:10])
    body = f"""
<div class="home">
  <aside class="rail">
    <div class="rail-h">Browse</div>
    <a class="rail-link active" href="/">All APIs<span>{n:,}</span></a>
    <div class="rail-h rail-sec">Collections</div>
    <a class="rail-link" href="/free-apis/">Free tier<span>{free_n:,}</span></a>
    <a class="rail-link" href="/no-auth-apis/">No authentication<span>{noauth_n:,}</span></a>
    <a class="rail-link" href="/openapi-apis/">OpenAPI<span>{spec_n:,}</span></a>
    <a class="rail-link" href="/mcp-apis/">MCP<span>{mcp_n:,}</span></a>
    <div class="rail-h rail-sec">Categories</div>
    {rail_cats}
    <a class="rail-link" href="/categories/" style="color:var(--dim)">All categories →</a>
  </aside>
  <div class="home-main">
    <h1 class="home-h1" style="font-size:clamp(26px,3.2vw,36px);max-width:17em;margin-top:14px">Public API terms, tracked over time.</h1>
    <p class="home-sub" style="font-size:15px;max-width:56ch">Authentication, pricing, free tiers, rate limits,
    OpenAPI, and MCP for <b>{n:,} APIs</b>. Sourced from vendor documentation and checked for changes.</p>
    <div class="cta-row" style="margin-bottom:0">
      <a class="btn solid inline" href="#browse">Browse APIs</a>
      <a class="linklike" href="/changes/" style="text-decoration:none">Recent changes →</a>
    </div>
    <p class="statmini"><b>{n:,}</b> APIs · <b>{pages_monitored:,}</b> source pages · Last source sweep <b>{escape(last_check_h)}</b></p>
    <div class="catlist mobcats">{mob_cats}<a class="chip cat" href="/categories/">all →</a></div>
    <div class="searchwrap" id="browse">
      <input class="search" id="q" type="search" placeholder="Search {n:,} APIs…" autocomplete="off">
      <span class="chip fchip" data-f="free">Free tier</span>
      <span class="chip fchip" data-f="rl">Rate limits</span>
      <span class="chip fchip" data-f="mcp">MCP</span>
      <button class="linklike" id="morebtn" type="button">More filters ▾</button>
      <span class="chip fchip mf" data-f="noauth" hidden>No auth</span>
      <span class="chip fchip mf" data-f="spec" hidden>OpenAPI</span>
      <span class="chip fchip mf" data-f="hi" hidden>High confidence</span>
      {chg_chip}
      <span class="nshow" id="nshowwrap" hidden><b id="nshow">{n:,}</b> shown</span>
    </div>
    <div class="table-wrap"><table id="apitable" class="collapsed">{TABLE_HEAD}<tbody>
{table_rows(recs_tbl, base, logo_limit=100, changed=changed_recent, xtra_after=12)}
</tbody></table><button class="showall" id="showall" type="button">Show all {n:,} APIs ↓</button></div>
    {sponsor_unit}
    <div class="sect">
      <h2>What the dataset shows</h2>
      <div class="findrow">
        <span><b>{free_pct}%</b> offer a free tier</span>
        <span><b>{mcp_pct}%</b> document MCP</span>
        <span><b>{rec_spec_pct}%</b> publish OpenAPI</span>
      </div>
      <p>Public APIs currently document MCP <b style="color:var(--ink)">{ratio}×</b> more often than
      OpenAPI — recomputed from the corpus on every build. <a href="/report/">See all findings →</a></p>
    </div>
    {chg_panel}
    <div class="sect why2col">
      <div>
        <div class="seclabel">Why API Terms</div>
        <h2>What API directories leave out</h2>
        <p>Most API directories tell you that an API exists. They rarely tell you how to
        authenticate, what the free tier includes, where the rate limits are, or whether any
        of those terms recently changed.</p>
        <p>API Terms collects those details from vendor documentation and links every field
        back to its source. We check those pages again over time and record what changed.
        When a vendor does not publish something, we leave it blank.</p>
        <p>It is useful when you are comparing APIs, running an integration in production,
        or building agents that need to choose between tools.</p>
      </div>
      {fieldcard}
    </div>
    <div class="panel osscard">
      <div class="seclabel">Open source</div>
      <h2>Inspect the full pipeline</h2>
      <p>The extractor, verifier, and change tracker are MIT-licensed. See how a record was
      produced, contribute a source, or run it on your own collection.</p>
      <div class="cta-row" style="margin:0">
        <a class="btn inline" href="{GITHUB}" target="_blank" rel="noopener">View source code</a>
        <a class="linklike" href="/methodology/" style="text-decoration:none">Read the methodology →</a>
      </div>
      <div class="oss-facts"><span>MIT licensed</span><span>Standard-library Python</span><span>Public change history</span></div>
    </div>
  </div>
</div>
<script>
var rows=[].slice.call(document.querySelectorAll("tbody tr")),
    q=document.getElementById("q"),
    chips=[].slice.call(document.querySelectorAll(".fchip")),
    tbl=document.getElementById("apitable"),
    showall=document.getElementById("showall");
function expand(){{tbl.classList.remove("collapsed");if(showall)showall.style.display="none"}}
if(showall)showall.addEventListener("click",expand);
function apply(){{
  var s=q.value.toLowerCase().trim(),
      f=chips.filter(function(c){{return c.classList.contains("on")}}).map(function(c){{return c.dataset.f}}),
      active=!!s||f.length>0, shown=0;
  if(active)expand();
  rows.forEach(function(r){{
    var ok=(!s||(r.dataset.s||"").indexOf(s)>-1)&&f.every(function(k){{return r.dataset[k]==="1"}});
    r.style.display=ok?"":"none"; if(ok)shown++;
  }});
  document.getElementById("nshow").textContent=shown.toLocaleString();
  document.getElementById("nshowwrap").hidden=!active;
}}
q.addEventListener("input",apply);
chips.forEach(function(c){{c.addEventListener("click",function(){{c.classList.toggle("on");apply()}})}});
var mb=document.getElementById("morebtn");
if(mb)mb.addEventListener("click",function(){{
  [].forEach.call(document.querySelectorAll(".fchip.mf"),function(c){{c.hidden=false}});
  mb.style.display="none";
}});
document.addEventListener("keydown",function(e){{
  if((e.metaKey||e.ctrlKey)&&e.key==="k"){{e.preventDefault();q.focus()}}}});
(function(){{
  var sub=new URLSearchParams(location.search).get("sub"), box=document.getElementById("feed-msg");
  if(!sub||!box) return;
  var form=box.parentNode.querySelector("form");
  if(sub==="ok"){{ box.textContent="You're on the list — we'll email the digest when it ships."; box.className="add-note ok"; if(form)form.style.display="none"; }}
  else {{ box.textContent="Couldn't sign you up just then — please try again."; box.className="add-note err"; }}
}})();
</script>"""
    return page(title, desc, f"{base}/", body, base)


FIELD_DOCS = [
    ("name", "Vendor / API display name"),
    ("what_it_does", "One-sentence answer-engine summary of the API"),
    ("base_url", "Root endpoint of the API"),
    ("auth_type", "none · api_key · bearer_token · oauth2 · basic · other"),
    ("free_tier", "What you get without paying, as the vendor states it"),
    ("pricing_model", "free · freemium · usage_based · subscription · enterprise"),
    ("pricing_details", "Plan names, prices and quotas, verbatim-adjacent"),
    ("rate_limits", "Documented request limits, per plan where stated"),
    ("openapi_spec_url", "Link to the machine-readable OpenAPI/Swagger spec"),
    ("mcp_server", "Documented Model Context Protocol server, if any"),
    ("evidence_url (per field)", "The exact vendor page that states each value"),
    ("confidence", "Extraction confidence: high · medium · low"),
    ("last_verified", "Date the record was last checked against its sources"),
]


def dataset_page(published, corpus_stats, base):
    n = len(published)
    url = f"{base}/dataset/"
    title = "The dataset — API Terms"
    desc = (f"{n} public APIs as structured JSONL: auth, pricing, free tier, rate "
            f"limits, spec, MCP — per-field source URLs, re-verified on a schedule.")
    rows = "\n".join(
        f'<div class="field"><div class="k">{escape(f)}</div>'
        f'<div class="v">{escape(d)}</div><span class="src none"></span></div>'
        for f, d in FIELD_DOCS)
    jsonld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": "API Terms — public API terms census",
        "description": desc, "url": url,
        "creator": {"@type": "Organization", "name": "API Terms", "url": base},
        "keywords": ["API", "pricing", "rate limits", "authentication", "OpenAPI", "MCP"],
        "distribution": [{"@type": "DataDownload",
                          "encodingFormat": "application/jsonl",
                          "contentUrl": f"{base}/data/sample.jsonl"}],
        "isAccessibleForFree": True,
    }
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Dataset</div>
<div class="kicker">Dataset</div>
<h1 style="font-size:26px;margin:0 0 8px">The census as data</h1>
<p class="sub">Everything on this site is rendered from one dataset: <b style="color:var(--ink)">{n:,}
records</b>, one per API, every non-null claim carrying the vendor URL that states it.
The full current snapshot is free to download. The paid products are built on top:
the change feed (what changed, when, with proof), history, and commercial licensing.</p>
<div class="cols" style="margin-top:22px">
<main>
  <div class="panel">
    <div style="padding:16px 26px 6px"><span class="kicker">Schema — one record per API</span></div>
    {rows}
  </div>
</main>
<aside>
  <div class="panel card">
    <h3>Get it</h3>
    <div class="code"><span class="c"># full current snapshot (JSONL)</span>
<span class="m">GET</span> {base}/data/sample.jsonl

<span class="c"># one record</span>
<span class="m">GET</span> {base}/api/stripe.com/record.json

<span class="c"># agent index</span>
<span class="m">GET</span> {base}/llms.txt</div>
  </div>
  <div class="panel card">
    <h3>Terms of use</h3>
    <ul>
      <li><span class="ck">▸</span><span>Free for evaluation and internal use, with attribution
      to <b>apiterms.com</b>.</span></li>
      <li><span class="ck">▸</span><span>Redistribution, resale, model training or bundling:
      <b>licensed separately</b> — <a href="/correct/">contact us</a>.</span></li>
      <li><span class="ck">▸</span><span>Re-verified on a schedule; see
      <a href="/methodology/">methodology</a>.</span></li>
    </ul>
    <a class="btn" href="/correct/">License the data / change feed</a>
  </div>
</aside>
</div>"""
    return page(title, desc, url, body, base, jsonld), url


def categories_page(cats_sorted, base):
    url = f"{base}/categories/"
    total = sum(len(rs) for _, rs in cats_sorted)
    title = f"API categories — {len(cats_sorted)} categories, {total:,} APIs | API Terms"
    desc = (f"All {total:,} tracked APIs across {len(cats_sorted)} categories, with "
            "verified auth, pricing and rate limits in each.")
    cards = []
    for c, rs in cats_sorted:
        free = sum(1 for r in rs if v(r, "free_tier"))
        mcp = sum(1 for r in rs if v(r, "mcp_server"))
        cards.append(
            f'<a class="panel card" style="display:block;margin:0" href="/category/{slugify(c)}/">'
            f'<h3 style="margin-bottom:8px">{escape(c)}</h3>'
            f'<div class="mono" style="font-size:12.5px;color:var(--body)">{len(rs)} APIs · '
            f'{free} free tier · {mcp} MCP</div></a>')
    grid = "\n".join(cards)
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Categories</div>
<div class="kicker">Categories</div>
<h1 style="font-size:26px;margin:0 0 8px">Every category</h1>
<p class="sub">{escape(desc)}</p>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-top:22px">
{grid}
</div>"""
    return page(title, desc, url, body, base), url


def collection_page(slug, name, phrase, recs, base, sponsors):
    url = f"{base}/{slug}/"
    n = len(recs)
    title = f"{name}: {n} verified ({time.strftime('%B %Y')}) | API Terms"
    desc = (f"{n} public APIs with {phrase} — verified against vendor docs, "
            f"with a source URL on every claim.")
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>{escape(name)}</div>
<div class="kicker">Collection</div>
<h1 style="font-size:26px;margin:0 0 8px">{escape(name)}</h1>
<p class="sub">{escape(desc)} Sorted by record completeness.</p>
<div class="table-wrap" style="margin-top:20px"><table>{TABLE_HEAD}<tbody>
{table_rows(recs, base, sponsors["featured"], logo_limit=100)}
</tbody></table></div>"""
    return page(title, desc, url, body, base), url


def sponsors_page(n_recs, cats, base, sponsors):
    url = f"{base}/sponsors/"
    title = "Sponsor API Terms — apiterms.com"
    desc = (f"Put your product in front of developers and agent builders comparing the "
            f"terms of {n_recs} public APIs. One disclosed slot per category.")
    def slot(c):
        sp = sponsors["categories"].get(slugify(c))
        return (f'<span class="pill sp">{escape(sp["name"])}</span>' if sp
                else '<span class="pill ok">open</span>')
    cat_rows = "\n".join(
        f'<tr><td class="name"><a href="/category/{slugify(c)}/">{escape(c)}</a></td>'
        f'<td>{len(rs)} APIs</td><td>{slot(c)}</td></tr>'
        for c, rs in cats)
    if FORMSPREE_PROJECT:
        # Copy note: rates are published above, so the button and the confirmation
        # must not promise to email a price the visitor can already read — they
        # promise availability and the traffic figures we deliberately don't publish.
        sponsor_cta = f"""<form action="{form_action('sponsor')}" method="POST"
    data-ok="Thanks — received. We'll come back by email with availability and current traffic figures.">
    <input type="hidden" name="_subject" value="Sponsorship inquiry — apiterms.com">
    <input class="fld" type="email" name="email" placeholder="you@company.com" required>
    <textarea class="fld" name="message" style="margin-top:8px"
     placeholder="Which category / featured listing are you interested in?"></textarea>
    <button class="btn solid" type="submit" style="margin-top:8px">Enquire about a slot</button>
    </form>"""
    else:
        sponsor_cta = '<a class="btn" href="/correct/">Contact us</a>'  # no email on site

    # Published rates. Every figure comes from sponsors.json — nothing is hardcoded
    # here, and an absent "rates" key renders nothing rather than a placeholder price.
    # Copy rule: only claims that are true right now. We publish corpus
    # numbers, which are computed at build time and verifiable on the site itself;
    # we do NOT publish traffic figures, because the site is young and an unverified
    # audience number on a page whose whole product is verified claims is a
    # self-inflicted wound. Point buyers at analytics on request instead.
    rates = sponsors.get("rates")
    rate_card = ""
    if rates and rates.get("items"):
        rows = "\n".join(
            f'<tr><td class="name">{escape(i["name"])}</td>'
            f'<td class="mono">{escape(i["price"])}</td>'
            f'<td>{escape(i.get("unit", ""))}</td></tr>'
            for i in rates["items"])
        note = (f'<p class="sub" style="margin:10px 0 0;font-size:13px">'
                f'{escape(rates["note"])}</p>' if rates.get("note") else "")
        rate_card = f"""<div class="panel card">
    <h3>Rates</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>Placement</th><th>Price</th><th></th></tr></thead>
      <tbody>{rows}</tbody></table></div>
    {note}
  </div>"""

    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Sponsors</div>
<div class="kicker">Sponsors</div>
<h1 style="font-size:26px;margin:0 0 8px">Sponsor API Terms</h1>
<p class="sub">API Terms is read by developers and AI-agent builders while they compare
API terms — auth, pricing, rate limits — right before they pick one. Two ways to be
visible at that moment:</p>
<div class="cols" style="margin-top:22px">
<main>
  <div class="panel card">
    <h3>Category sponsor</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>One slot per category</b> — a disclosed sponsor bar
      on the category page, above the comparison table.</span></li>
      <li><span class="ck">▸</span><span>Your name, link and one-line pitch. Marked
      <b>SPONSOR</b>, always.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>Featured listing</h3>
    <ul>
      <li><span class="ck">▸</span><span>Your API <b>pinned to the top of its category
      table</b>, marked <b>FEATURED</b>.</span></li>
      <li><span class="ck">▸</span><span>The record itself stays identical — same fields,
      same evidence links, same confidence score.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>Who you reach</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>Developers evaluating an API</b> — checking auth,
      pricing and limits right before choosing one.</span></li>
      <li><span class="ck">▸</span><span><b>Teams running APIs in production</b> — watching for
      term changes before they break an integration or a budget.</span></li>
      <li><span class="ck">▸</span><span><b>Data teams &amp; agent builders</b> — consuming the
      dataset and change feed to decide what their agents call.</span></li>
    </ul>
  </div>
  {rate_card}
  <div class="panel card">
    <h3>What sponsorship never buys</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>Placement, never data.</b> No sponsor can change a
      record, a field value, a confidence score, or who gets listed.</span></li>
      <li><span class="ck">▸</span><span>Every claim on every page keeps its evidence URL —
      that's the product.</span></li>
    </ul>
    {sponsor_cta}
  </div>
</main>
<aside>
  <div class="table-wrap"><table>
  <thead><tr><th>Category</th><th>Size</th><th>Slot</th></tr></thead>
  <tbody>{cat_rows}</tbody></table></div>
</aside>
</div>"""
    return page(title, desc, url, body, base), url


def correct_page(base):
    """Correction / claim form. ?domain=X&kind=correction|claim prefills via JS."""
    url = f"{base}/correct/"
    title = "Corrections & vendor claims — API Terms"
    desc = ("Spotted a wrong field? Run one of these APIs? Every record links its "
            "sources — tell us what changed and we re-verify.")
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span><span id="crumb-leaf">Corrections</span></div>

<div id="hero-correct">
  <div class="kicker">Accuracy</div>
  <h1 style="font-size:26px;margin:0 0 8px">Corrections &amp; vendor claims</h1>
  <p class="sub">One wrong pricing claim is one too many. Every field on every record
  links the page it came from — if reality moved, tell us and we re-verify against
  the source. API vendors: use the same form to claim your page.</p>
</div>

<div id="hero-claim" style="display:none">
  <div class="kicker">For API vendors</div>
  <h1 style="font-size:26px;margin:0 0 8px">Claim your API&#39;s page</h1>
  <p class="sub">This is your listing on the live terms layer that developers and AI agents
  check before they pick an API. Claim it to confirm it&#39;s right, stay ahead of changes,
  and unlock a verified badge — free.</p>
  <div class="cols" style="margin-top:20px;align-items:stretch">
    <div class="panel card" style="margin:0">
      <h3>How claiming works</h3>
      <ul>
        <li><span class="ck">1</span><span><b>You verify ownership.</b> Submit from a company
        address (or point us at the docs you control) so we know it&#39;s really you.</span></li>
        <li><span class="ck">2</span><span><b>We confirm the record with you.</b> We walk the
        fields against your own pages and fix anything stale — every value keeps its evidence URL.</span></li>
        <li><span class="ck">3</span><span><b>You get verified.</b> An optional verified badge,
        change alerts when your terms move, and the option to feature your listing.</span></li>
      </ul>
      <p class="sub" style="margin:4px 0 0;font-size:11.5px;color:var(--dim)">Placement never buys a
      field: claiming keeps your record accurate, it doesn&#39;t let you rewrite it.</p>
    </div>
    <div class="panel card" style="margin:0">
      <h3>Why claim it</h3>
      <ul>
        <li><span class="ck">▸</span><span>Developers and agents compare terms here <b>right before
        they choose</b> — an accurate, verified listing wins the pick.</span></li>
        <li><span class="ck">▸</span><span>Vendors change pricing and limits quietly; a claim means
        <b>you hear about our next detected change first</b>.</span></li>
        <li><span class="ck">▸</span><span>Setup takes one message. We reply, confirm, and mark
        the page verified.</span></li>
      </ul>
    </div>
  </div>
</div>

<div class="panel card" style="max-width:560px;margin-top:22px">
  <h3 id="form-hd">What&#39;s wrong (or yours)?</h3>
  <form action="{form_action('correction')}" method="POST"
   data-ok="Thanks — received. We re-verify against the vendor's own pages before changing any record, and we'll reply by email.">
    <input class="fld" type="text" name="domain" id="f-domain" placeholder="api domain, e.g. stripe.com" required>
    <select class="fld" name="kind" id="f-kind" style="margin-top:8px">
      <option value="correction">Correction — a field is wrong or outdated</option>
      <option value="claim">Claim — this is my API's page</option>
      <option value="add">Add — an API that's missing from the census</option>
      <option value="other">Something else</option>
    </select>
    <input class="fld" type="email" name="email" id="f-email" placeholder="you@company.com" required style="margin-top:8px">
    <textarea class="fld" name="message" id="f-msg" style="margin-top:8px"
     placeholder="Which field, what it should say, and (ideally) the URL that proves it."></textarea>
    <button class="btn solid" type="submit" id="f-submit" style="margin-top:8px">Send</button>
  </form>
</div>
<script>
var q=new URLSearchParams(location.search);
if(q.get("domain"))document.getElementById("f-domain").value=q.get("domain");
var kind=q.get("kind");
if(kind)document.getElementById("f-kind").value=kind;
if(kind==="claim"){{
  document.getElementById("hero-correct").style.display="none";
  document.getElementById("hero-claim").style.display="";
  document.getElementById("crumb-leaf").textContent="Claim your page";
  document.getElementById("form-hd").textContent="Start your claim";
  document.getElementById("f-email").placeholder="you@yourcompany.com (a company address helps us verify)";
  document.getElementById("f-msg").placeholder="Confirm you run this API and note anything that's out of date. Link us to a page you control (docs, dashboard) so we can verify ownership.";
  document.getElementById("f-submit").textContent="Submit claim →";
  document.title="Claim your API's page — API Terms";
}}
</script>"""
    return page(title, desc, url, body, base), url


def add_page(base):
    """Suggest-an-API page. Contributors expand COVERAGE (a domain to cover); they never
    write field values — the pipeline crawls, extracts with evidence, and QA-gates it, so
    the evidence-or-null guarantee (the moat) holds even for community submissions."""
    url = f"{base}/add/"
    title = "Add an API to the census — API Terms"
    desc = ("Missing an API you use? Tell us the domain — we crawl it, extract the terms "
            "with a source link on every field, and QA it before it publishes.")
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Add an API</div>
<div class="kicker">Community coverage</div>
<h1 style="font-size:26px;margin:0 0 8px">Add an API to the census</h1>
<p class="sub">Using an API we don't cover yet? Drop the domain below. <b>You suggest the API;
our pipeline does the verifying</b> — it crawls the vendor's own docs, extracts auth, pricing,
free tier, rate limits, spec and MCP with <b>a source URL on every field</b>, and runs it
through the same QA gate as everything else. That's how community coverage stays trustworthy:
you expand what we track, never the values themselves.</p>
<div class="cols" style="margin-top:22px">
  <div class="panel card" style="max-width:520px">
    <h3>Suggest an API</h3>
    <div id="add-msg" role="status"></div>
    <form action="/.netlify/functions/add-api" method="POST">
      <input type="hidden" name="kind" value="add">
      <input type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"
       style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">
      <input class="fld" type="text" name="domain" placeholder="api domain, e.g. resend.com" required>
      <input class="fld" type="url" name="docs" placeholder="docs / pricing URL (optional, speeds it up)" style="margin-top:8px">
      <input class="fld" type="email" name="email" placeholder="you@company.com (we'll tell you when it's live)" required style="margin-top:8px">
      <textarea class="fld" name="message" style="margin-top:8px"
       placeholder="Anything that helps — what the API does, where the pricing page is, etc. (optional)"></textarea>
      <button class="btn solid" type="submit" style="margin-top:8px">Add it to the queue →</button>
    </form>
    <p class="sub" style="font-size:12px;margin:10px 0 0;color:var(--dim)">Submit a domain and
    it's queued automatically — the next weekly run crawls, verifies and publishes it.</p>
  </div>
  <aside>
    <div class="panel card">
      <h3>How it works</h3>
      <ul>
        <li><span class="ck">1</span><span>You submit a <b>domain</b> — not data.</span></li>
        <li><span class="ck">2</span><span>Our crawler reads the vendor's own docs and pricing pages.</span></li>
        <li><span class="ck">3</span><span>Every field extracted gets <b>the source URL that states it</b>; anything undocumented stays <span class="mono">null</span>.</span></li>
        <li><span class="ck">4</span><span>It passes the QA gate, publishes a record page, and is <b>re-verified weekly</b> from then on — automatically.</span></li>
      </ul>
      <p class="sub" style="font-size:12.5px;margin:12px 0 0">Want to see exactly how a record is
      built and verified first? Read the <a href="/methodology/">methodology</a>.</p>
    </div>
  </aside>
</div>
<script>
(function(){{
  var p=new URLSearchParams(location.search), box=document.getElementById('add-msg');
  if(!box) return;
  var ok=p.get('ok'), err=p.get('err');
  function show(t,cls){{ box.textContent=t; box.className='add-note '+cls; }}
  if(ok) show('Thanks — queued. We crawl and verify it on the next weekly run, then it goes live.','ok');
  else if(err==='domain') show('That doesn\\'t look like a valid domain. Try just the host, e.g. resend.com','err');
  else if(err) show('Something went wrong saving that — please try again in a moment.','err');
}})();
</script>"""
    return page(title, desc, url, body, base), url


def report_page(recs, base):
    """State of the API Economy — data story computed live from the corpus, so every
    figure updates as the census grows (proof-engine ethos: never a stale number)."""
    import collections
    url = f"{base}/report/"
    n = len(recs)

    def cnt(f):
        return sum(1 for r in recs if v(r, f))

    mcp, spec = cnt("mcp_server"), cnt("openapi_spec_url")
    mcp_pct, spec_pct = round(100 * mcp / n, 1), round(100 * spec / n, 1)
    # one decimal, same corpus + same criteria as the homepage band — the two surfaces
    # must never show different ratios for the same finding
    ratio = round(mcp / spec, 1) if spec else 0
    ver = [r for r in recs if r.get("confidence") in ("high", "medium")]
    nv = max(len(ver), 1)
    free_ver = round(100 * sum(1 for r in ver if v(r, "free_tier")) / nv)
    rl_ver = round(100 * sum(1 for r in ver if v(r, "rate_limits")) / nv)

    auth = collections.Counter(v(r, "auth_type") for r in recs if v(r, "auth_type"))
    at = max(sum(auth.values()), 1)
    pm = collections.Counter(v(r, "pricing_model") for r in recs if v(r, "pricing_model"))
    pt = max(sum(pm.values()), 1)

    bycat = {}
    for r in recs:
        bycat.setdefault(norm_category(r.get("category")), []).append(r)
    catrows = sorted(
        ((round(100 * sum(1 for r in rs if v(r, "free_tier")) / len(rs)), c, len(rs))
         for c, rs in bycat.items() if len(rs) >= 15), reverse=True)

    # llms.txt adoption: from the local classify output when present, else from the
    # COMMITTED probe-stats snapshot (data/probe_stats.json) — CI builds don't have
    # seed_classified.jsonl (gitignored pipeline state) and were publishing a false
    # "0%" before this fallback existed (caught live 2026-07-17).
    cl_path = ROOT / "data" / "seed_classified.jsonl"
    ps_path = ROOT / "data" / "probe_stats.json"
    if cl_path.exists():
        alive = [c for c in (json.loads(l) for l in cl_path.open()) if c.get("alive")]
        llms_pct = round(100 * sum(1 for c in alive if c.get("llms_txt")) / max(len(alive), 1))
    elif ps_path.exists():
        ps = json.loads(ps_path.read_text())
        llms_pct = round(100 * ps["llms"] / max(ps["alive"], 1))
    else:
        llms_pct = None
    if llms_pct is None:
        llms_block = ""
    else:
        llms_block = f"""<h2 style="font-size:21px;margin:44px 0 8px">Most APIs are still hard for machines to discover</h2>
<p>Across the live API domains we checked, only {llms_pct}% publish an <span class="mono">llms.txt</span>
file. For most public APIs, an agent still has to read the same documentation pages a human
would. That's the gap this census is trying to close.</p>"""

    def bar(label, pct, cls="b"):
        fillcol = ("linear-gradient(90deg,var(--blue),var(--bluehot))" if cls == "mcp"
                   else "var(--lineh)" if cls == "spec" else "var(--blue)")
        return (f'<div style="margin-bottom:16px"><div style="display:flex;justify-content:space-between;'
                f'font-family:var(--mono);font-size:13px;margin-bottom:6px"><span>{escape(label)}</span>'
                f'<span style="color:var(--ink);font-weight:600">{pct}%</span></div>'
                f'<div style="height:20px;background:var(--void);border:1px solid var(--line);position:relative">'
                f'<div style="position:absolute;inset:0;width:{min(pct*2,100)}%;background:{fillcol}"></div></div></div>')

    auth_bars = "".join(bar(hum(k) or "unknown", round(100 * c / at)) for k, c in auth.most_common(4))
    price_bars = "".join(bar(hum(k) or "unknown", round(100 * c / pt)) for k, c in pm.most_common(4))
    cat_rows = "\n".join(
        f'<tr><td>{escape(c)}</td><td class="n">{ft}%</td>'
        f'<td><span style="display:inline-block;height:9px;background:var(--blue);'
        f'vertical-align:middle;width:{max(int(ft*1.1),4)}px"></span></td></tr>'
        for ft, c, _ in catrows)

    title = "The State of the API Economy 2026 — API Terms"
    rl_pct = round(100 * sum(1 for r in recs if v(r, "rate_limits")) / n)
    authd_pct = round(100 * sum(1 for r in recs if v(r, "auth_type")) / n)
    freed_pct = round(100 * sum(1 for r in recs if v(r, "free_tier")) / n)
    pmd_pct = round(100 * sum(1 for r in recs if v(r, "pricing_model")) / n)
    desc = (f"We verified the access terms of {n:,} public APIs. Only {rl_pct}% document their "
            f"rate limits, {authd_pct}% their authentication, {freed_pct}% a free tier. "
            "Every figure source-linked.")
    jsonld = {
        "@context": "https://schema.org", "@type": "Report",
        "name": "The State of the API Economy 2026", "url": url,
        "datePublished": time.strftime("%Y-%m-%d"),
        "publisher": {"@type": "Organization", "name": "API Terms", "url": base},
        "description": desc,
    }
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Report</div>
<div class="seclabel">State of the API economy · 2026</div>
<h1 style="font-size:clamp(25px,3.6vw,36px);line-height:1.2;margin:0 0 16px;max-width:26em">
The terms that decide whether you can use an API are barely documented.</h1>
<p class="sub" style="font-size:17px;max-width:42em">We checked the access terms of {n:,} public
APIs — authentication, pricing, free tiers, rate limits, specs, and MCP servers. Every value
links back to the vendor's own documentation. Here's what the machine-readable side of the API
economy looks like in mid-2026.</p>

<div class="panel card" style="padding:24px 28px;margin:30px 0;max-width:620px">
  <p style="margin:0 0 18px;font-size:15px;color:var(--ink);line-height:1.5">Fewer than
  <span class="mono" style="font-size:24px;color:var(--bluehot);padding:0 2px">1 in 4</span>
  public APIs document their rate limits — the number an integration hits first.</p>
  {bar("Documents rate limits", rl_pct)}
  {bar("Documents authentication", authd_pct)}
  {bar("Documents a free tier", freed_pct)}
  {bar("Documents a pricing model", pmd_pct)}
</div>

<p>These are the questions every integration starts with — how do I auth, what does it cost,
what's free, where are the limits — and for most public APIs the vendor's own pages answer
only some of them. That documentation gap is the reason this census exists: we record what is
stated, link the page that states it, and publish an honest <span class="mono">null</span>
for the rest.</p>

<p style="font-size:13.5px;color:var(--dim)">A note on a retired claim: an earlier version of
this report led with an MCP-vs-OpenAPI ratio. We pulled it — a direct probe of live domains
finds far more OpenAPI specs than our extraction captured, so the two numbers were not
comparable floors. When our own data can't support a claim, the claim goes.</p>

<h2 style="font-size:21px;margin:44px 0 8px">Free tiers are normal now</h2>
<p>Among the APIs where we could fully verify the terms, {free_ver}% offer some kind of free
tier and {rl_ver}% publish their rate limits. Free access isn't unusual anymore — it's what
developers expect.</p>
<p>The strange part: government and open-data APIs are among the <em>least</em> likely to
clearly document a usable free tier. The APIs may be free, but whether you can rely on them in
production is often simply not stated.</p>

<div class="table-wrap tick" style="margin:22px 0">
  <table>
    <thead><tr><th>Category</th><th style="text-align:right">Free-tier rate</th><th></th></tr></thead>
    <tbody>{cat_rows}</tbody>
  </table>
</div>

<h2 style="font-size:21px;margin:44px 0 8px">Authentication is simpler than it looks</h2>
<p>For APIs that document authentication, API keys still dominate — and almost one in five APIs
needs no authentication at all. OAuth gets a lot of attention in integration guides, but it's
still the minority case. For agents calling lots of different APIs, that's good news: most
authentication comes down to a single static credential.</p>
<div class="panel card" style="margin:20px 0">{auth_bars}</div>

<h2 style="font-size:21px;margin:44px 0 8px">Pricing has settled into a pattern</h2>
<p>Most APIs are either freemium or completely free. Pure pay-to-play is relatively uncommon,
and usage-based pricing is still a minority. The default model is pretty clear by now: give
people a free bucket, then charge based on usage, users, or features.</p>
<div class="panel card" style="margin:20px 0">{price_bars}</div>

{llms_block}

<div class="panel card tick" style="margin:34px 0">
  <h3 style="font-size:14px;font-weight:700;color:var(--ink);font-family:var(--sans);letter-spacing:0;text-transform:none">How we measured this — and what these numbers do and don't say</h3>
  <ul style="margin:8px 0 0;padding-left:0;list-style:none">
    <li style="margin-bottom:9px"><span class="ck">▸</span> Every value cites the
    exact vendor page that states it; a deterministic check rejects any citation we didn't actually read.</li>
    <li style="margin-bottom:9px"><span class="ck">▸</span> These are documented rates — floors.
    A null means "not stated where we looked," not "doesn't exist."</li>
    <li style="margin-bottom:9px"><span class="ck">▸</span> "Documents an MCP server" counts
    documented references, not only live endpoints; the corpus is broad but not every public API on earth;
    multi-product vendors are one record per domain today.</li>
  </ul>
</div>

<div class="panel card tick" style="text-align:center;margin:40px 0 0;padding:28px">
  <h2 style="font-size:20px;margin:6px 0 10px">Read the source on any of these APIs</h2>
  <p class="sub" style="max-width:44ch;margin:0 auto 18px">Every figure here is backed by {n:,} records
  with per-field evidence links, re-verified on a schedule.</p>
  <a class="btn solid" style="width:auto;display:inline-block;padding:12px 22px" href="/">Browse the census →</a>
  <a class="btn" style="width:auto;display:inline-block;padding:12px 22px" href="/dataset/">Get the dataset</a>
</div>"""
    return page(title, desc, url, body, base, jsonld), url


def iso_week(date_str):
    """'2026-07-19' -> ('2026-W29', sortable key). Groups events by the week detected."""
    try:
        import datetime
        d = datetime.date.fromisoformat(date_str)
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}", (y, w)
    except Exception:
        return date_str, (0, 0)


def event_line(e, base):
    old = ('<span style="color:var(--ghost);font-style:italic">not documented</span>'
           if e["old"] is None else f'<span class="del">{escape(str(e["old"])[:80])}</span>')
    new = ('<span style="color:var(--ghost);font-style:italic">removed</span>'
           if e["new"] is None else f'<span class="add">{escape(str(e["new"])[:80])}</span>')
    sig = SIG_LABEL.get(e["significance"], "Details")
    src = (f'<a class="src" href="{escape(e["evidence_url"])}" rel="nofollow">source ↗</a>'
           if e.get("evidence_url") else "")
    return (f'<div class="chg-row">'
            f'<div class="chg-hd"><a href="/api/{escape(e["domain"])}/" class="chg-dom">{escape(e["domain"])}</a>'
            f'<span class="chip sig">{escape(sig)}</span>'
            f'<span class="chg-date mono">{escape(e["detected"])}</span></div>'
            f'<div class="chg-diff mono">{old} <span style="color:var(--dim)">→</span> {new} {src}</div>'
            f'</div>')


def changes_page(events, base):
    url = f"{base}/changes/"
    title = "API change feed — pricing, rate-limit & auth changes | API Terms"
    desc = ("Structured, source-linked changes to public API terms — pricing, rate limits, "
            "auth, free tiers — detected by re-verifying every record on a schedule.")
    # newest first, grouped by ISO week
    evs = sorted(events, key=lambda e: e["detected"], reverse=True)
    weeks = {}
    for e in evs:
        wk, key = iso_week(e["detected"])
        weeks.setdefault(wk, (key, []))[1].append(e)
    ordered = sorted(weeks.items(), key=lambda kv: kv[1][0], reverse=True)

    if not evs:
        inner = f"""
<div class="panel card tick" style="text-align:center;padding:40px 26px">
  <div style="font-family:var(--mono);color:var(--add);font-size:12px;letter-spacing:.1em;text-transform:uppercase">● Baseline established · {BASELINE_LABEL}</div>
  <h2 style="font-family:var(--mono);font-size:20px;margin:14px 0 8px">Change tracking is live.</h2>
  <p class="sub" style="max-width:46ch;margin:0 auto">Every record is snapshotted at its source.
  When a vendor changes pricing, a rate limit, auth or a free tier, the re-verification pass
  catches the diff and it appears here — <b>field, old value, new value, source</b>. This page
  fills as the corpus is re-checked. Want it pushed to you instead of pulled?</p>
  <a class="btn solid" style="width:auto;display:inline-block;padding:11px 20px;margin-top:18px" href="/#feed">Get the change feed →</a>
</div>"""
    else:
        blocks = []
        for wk, (_, wevs) in ordered:
            rows = "\n".join(event_line(e, base) for e in wevs)
            napi = len({e["domain"] for e in wevs})
            blocks.append(f'<div class="chg-week"><div class="kicker" style="margin:26px 0 10px">'
                          f'{escape(wk)} · {len(wevs)} change{"s" if len(wevs) != 1 else ""} '
                          f'across {napi} API{"s" if napi != 1 else ""}</div>{rows}</div>')
        inner = "\n".join(blocks)

    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Changes</div>
<div class="kicker">The change feed</div>
<h1 style="font-size:26px;margin:0 0 8px">What changed in the API economy</h1>
<p class="sub" style="max-width:44em">Vendors reprice, cut free tiers and tighten limits quietly.
We re-verify every record against its source and log the diffs here — the freshness apis.guru
never had. <a href="/changes.xml">RSS ↗</a></p>
<div style="margin-top:22px">{inner}</div>"""
    return page(title, desc, url, body, base), url


def changes_xml(events, base):
    """RSS 2.0 of change events — free distribution for agents and readers."""
    evs = sorted(events, key=lambda e: e["detected"], reverse=True)[:100]
    items = []
    for e in evs:
        old = "not documented" if e["old"] is None else str(e["old"])
        new = "removed" if e["new"] is None else str(e["new"])
        titl = f'{e["domain"]} — {SIG_LABEL.get(e["significance"], e["field"])} changed'
        body = f'{e["field"]}: {old} → {new}'
        link = f'{base}/api/{e["domain"]}/'
        items.append(
            f"<item><title>{escape(titl)}</title><link>{link}</link>"
            f"<guid isPermaLink=\"false\">{escape(e['domain']+'-'+e['field']+'-'+e['detected'])}</guid>"
            f"<description>{escape(body)}</description></item>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<rss version="2.0"><channel>'
            f'<title>API Terms — change feed</title>'
            f'<link>{base}/changes/</link>'
            f'<description>Structured changes to public API terms, verified at the source.</description>'
            f'{"".join(items)}</channel></rss>')


def methodology_page(funnel, base):
    url = f"{base}/methodology/"
    title = "Methodology — how records are verified | API Terms"
    desc = ("How API Terms extracts, sources and re-verifies every record: "
            "vendor-pages-only sourcing, per-field evidence URLs, honest nulls, "
            "regression-guarded QA, and what the numbers mean.")
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Methodology</div>
<div class="kicker">Methodology</div>
<h1 style="font-size:26px;margin:0 0 8px">How this data is made</h1>
<p class="sub">The census is only worth anything if you can check it. This page is the
contract: where values come from, what "verified" means, what a null means, and where
the current limitations are.</p>
<div class="cols" style="margin-top:22px">
<main>
  <div class="panel card">
    <h3>Sources</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>Vendor pages only.</b> Every value is extracted from
      the API vendor's own documentation, pricing or terms pages — never from third-party
      directories, blogs or forums.</span></li>
      <li><span class="ck">▸</span><span><b>Per-field evidence.</b> Each non-null field carries the
      URL of the exact page that states it. A record whose extraction cites a page we did
      not crawl is <b>rejected automatically</b> — fabricated sources cannot ship.</span></li>
      <li><span class="ck">▸</span><span><b>Polite crawling.</b> Honest user-agent, low request
      rates, and robots.txt respected — pages a site disallows are treated as unavailable
      and marked, never fetched. Public docs and pricing pages that plain HTTP can't read (JavaScript-rendered
      or bot-manager defaults) are fetched through a standard rendering service
      (Firecrawl) — public pages only, never logins or paywalls, capped per domain.
      Values are still never guessed: no readable page, no record.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>What a null means</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>"not documented"</b> on a record: we crawled the
      vendor's pages and found no statement for that field. That is a finding, not a
      gap — an agent should know a vendor documents no rate limit.</span></li>
      <li><span class="ck">▸</span><span><b>Tracked, no record:</b> {funnel['walled']} of
      {funnel['tracked']:,} tracked domains currently block crawling or serve JS-only docs.
      They are retried every cycle and never published as guesses.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>Verification &amp; QA</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>Re-verification runs the snapshot clock</b>: source
      pages are re-fetched and diffed against the stored snapshot, and any changed source
      triggers re-extraction — the diffs feed the <a href="/changes/">change feed</a>. Each
      record shows its own <span class="mono">last_verified</span> date.</span></li>
      <li><span class="ck">▸</span><span><b>{funnel['assertions']} golden assertions</b> guard
      hand-audited records (Stripe, GitHub, OpenAI, Slack…): any build in which one of those
      verified fields regresses to null <b>fails and cannot deploy</b>.</span></li>
      <li><span class="ck">▸</span><span><b>Confidence</b> (high · medium · low) is currently
      record-level, assigned at extraction; field-level confidence is on the roadmap.</span></li>
      <li><span class="ck">▸</span><span><b>Corrections:</b> every page links a
      <a href="/correct/">correction form</a>; reports are reviewed by a human and re-verified
      against the vendor source before any change.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>Independence</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>Placement, never data.</b> Sponsorship and featured
      listings buy disclosed visibility. No sponsor can change a record, a field value, a
      confidence score, or who gets listed. See <a href="/sponsors/">sponsors</a>.</span></li>
    </ul>
  </div>
  <div class="panel card">
    <h3>Known limitations</h3>
    <ul>
      <li><span class="ck">▸</span><span>Multi-product vendors (AWS, Google, Twilio…) are
      currently one record per domain; values are scoped to the primary public API and the
      provider→product hierarchy is in progress.</span></li>
      <li><span class="ck">▸</span><span>Category assignment is automated and being
      human-reviewed; expect occasional misfiles until that pass completes.</span></li>
      <li><span class="ck">▸</span><span>JS-only documentation sites limit coverage for some
      vendors (marked in the funnel above).</span></li>
    </ul>
  </div>
</main>
<aside>
  <div class="panel card">
    <h3>The funnel today</h3>
    <ul>
      <li><span class="ck">▸</span><span><b>{funnel['tracked']:,}</b> alive API domains tracked</span></li>
      <li><span class="ck">▸</span><span><b>{funnel['records']:,}</b> records extracted &amp; QA'd</span></li>
      <li><span class="ck">▸</span><span><b>{funnel['indexable']:,}</b> records with ≥4 verified
      fields (indexed)</span></li>
      <li><span class="ck">▸</span><span><b>{funnel['sparse']:,}</b> sparse records (published,
      noindexed)</span></li>
      <li><span class="ck">▸</span><span><b>{funnel['walled']}</b> domains walled/JS-only —
      tracked, no record</span></li>
      <li><span class="ck">▸</span><span><b>0</b> QA criticals tolerated per build</span></li>
    </ul>
  </div>
</aside>
</div>"""
    return page(title, desc, url, body, base), url


# ---------------------------------------------------------------- build

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://apiterms.com",
                    help="canonical base URL (no trailing slash)")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    recs = [json.loads(l) for l in CENSUS.open()]
    for r in recs:
        r["_category"] = norm_category(r.get("category"))
    published = sorted(recs, key=lambda r: (-filled(r), r["domain"]))
    lc = max((r.get("last_verified") or "" for r in recs), default="")
    try:
        import datetime
        lc = datetime.date.fromisoformat(lc).strftime("%b %-d, %Y")
    except Exception:
        pass
    STATS.update(n=len(published), last=lc)
    n_indexable = sum(1 for r in published if filled(r) >= INDEX_MIN_FIELDS)

    # corpus stats from classify output; CI builds fall back to the COMMITTED probe
    # snapshot (data/probe_stats.json) — without it the methodology funnel published
    # tracked=1,418 / walled=0 on prod (caught live 2026-07-17).
    classified = ROOT / "data" / "seed_classified.jsonl"
    probe_snap = ROOT / "data" / "probe_stats.json"
    tracked = llms = spec = 0
    if classified.exists():
        for line in classified.open():
            c = json.loads(line)
            tracked += bool(c.get("alive"))
            llms += bool(c.get("llms_txt"))
            spec += bool(c.get("spec_url") or c.get("openapi_probe"))
        total = sum(1 for _ in classified.open())
    elif probe_snap.exists():
        ps = json.loads(probe_snap.read_text())
        total, tracked, llms, spec = ps["total"], ps["alive"], ps["llms"], ps["spec"]
    else:
        total = tracked = len(recs)
    corpus = {"tracked": tracked, "llms_pct": round(100 * llms / max(total, 1), 1),
              "spec_pct": round(100 * spec / max(total, 1), 1)}

    # freshness layer 2 — the change ledger (empty until the first re-verification pass)
    changelog = [json.loads(l) for l in CHANGELOG.open()] if CHANGELOG.exists() else []
    hist_by_domain = {}
    for e in changelog:
        hist_by_domain.setdefault(e["domain"], []).append(e)
    import datetime
    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    changed_recent = {e["domain"] for e in changelog if e.get("detected", "") >= cutoff}

    # live-status line: how many source pages the tracker actually monitors
    sig_path = ROOT / "data" / "page_signatures.json"
    pages_monitored = 0
    if sig_path.exists():
        pages_monitored = sum(len(p) for p in json.loads(sig_path.read_text()).values())

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    (DIST / "style.css").write_text(CSS)
    (DIST / "favicon.svg").write_text(FAVICON)

    urls = [f"{base}/"]

    # record pages + per-record JSON (all records; sitemap = indexable only)
    for r in published:
        html, url = record_page(r, base, history=hist_by_domain.get(r["domain"]))
        d = DIST / "api" / r["domain"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(html)
        clean = {k: r[k] for k in r if not k.startswith("_")}
        (d / "record.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1))
        if filled(r) >= INDEX_MIN_FIELDS:
            urls.append(url)

    # category pages (guaranteed non-empty buckets)
    sponsors = load_sponsors()
    cats = {}
    for r in published:
        cats.setdefault(r["_category"], []).append(r)
    cats_sorted = sorted(cats.items(), key=lambda kv: -len(kv[1]))
    for cat, rs in cats_sorted:
        html, url = category_page(cat, rs, base, sponsors)
        d = DIST / "category" / slugify(cat)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(html)
        urls.append(url)

    # sponsors page (placement offer; never touches records)
    html, url = sponsors_page(len(published), cats_sorted, base, sponsors)
    d = DIST / "sponsors"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # correction / claim form page
    html, url = correct_page(base)
    d = DIST / "correct"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # add-an-API (community coverage) page
    html, url = add_page(base)
    d = DIST / "add"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # 404 (Netlify serves /404.html for any missing path). Not in the sitemap, noindexed.
    body_404 = f"""
<div style="padding:70px 0 30px;max-width:34em">
  <div class="kicker">404</div>
  <h1 style="font-size:28px;margin:0 0 10px">No record at this address.</h1>
  <p class="sub">The page may have moved, or we may not cover that API yet.</p>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">
    <a class="btn solid" style="width:auto;display:inline-block;padding:11px 20px" href="/">Search the census</a>
    <a class="btn" style="width:auto;display:inline-block;padding:11px 20px" href="/add/">+ Add the missing API</a>
  </div>
</div>"""
    (DIST / "404.html").write_text(page("Page not found — API Terms",
        "No record at this address.", f"{base}/404.html", body_404, base, noindex=True))

    # dataset landing page
    html, url = dataset_page(published, corpus, base)
    d = DIST / "dataset"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # categories index
    html, url = categories_page(cats_sorted, base)
    d = DIST / "categories"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # curated collections
    for slug, cname, phrase, pred in COLLECTIONS:
        matches = [r for r in published if pred(r)]
        html, url = collection_page(slug, cname, phrase, matches, base, sponsors)
        d = DIST / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(html)
        urls.append(url)

    # methodology
    n_index = sum(1 for r in published if filled(r) >= INDEX_MIN_FIELDS)
    funnel = {"tracked": corpus["tracked"], "records": len(published),
              "indexable": n_index, "sparse": len(published) - n_index,
              "walled": corpus["tracked"] - len(published),
              "assertions": len(json.loads((ROOT / "data" / "golden_assertions.json")
                                           .read_text())["assertions"])}
    html, url = methodology_page(funnel, base)
    d = DIST / "methodology"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # report — State of the API Economy (data story over the full corpus)
    html, url = report_page(published, base)
    d = DIST / "report"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)

    # change feed (freshness layer 2) — page + RSS
    html, url = changes_page(changelog, base)
    d = DIST / "changes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html)
    urls.append(url)
    (DIST / "changes.xml").write_text(changes_xml(changelog, base))

    # index
    (DIST / "index.html").write_text(index_page(
        published, cats_sorted, base, corpus, changelog, sponsors,
        pages_monitored, changed_recent))

    # sample dataset (free funnel) = published records verbatim
    (DIST / "data").mkdir(exist_ok=True)
    with (DIST / "data" / "sample.jsonl").open("w") as f:
        for r in published:
            f.write(json.dumps({k: r[k] for k in r if not k.startswith("_")},
                               ensure_ascii=False) + "\n")

    # sitemap / robots / llms.txt
    today = time.strftime("%Y-%m-%d")
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"<url><loc>{u}</loc><lastmod>{today}</lastmod></url>" for u in urls]
    sm.append("</urlset>")
    (DIST / "sitemap.xml").write_text("\n".join(sm))
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")

    lt = ["# API Terms", "",
          "> Auth, pricing, free tiers, rate limits, specs and MCP servers for every "
          "public API — one structured record each, a source URL on every field, "
          "re-verified on a schedule.", "",
          f"- [Directory]({base}/): all published records",
          f"- [Change feed]({base}/changes/): what changed in API terms ([RSS]({base}/changes.xml))",
          f"- [Add an API]({base}/add/): suggest an API to cover — we crawl and verify it",
          f"- [State of the API Economy report]({base}/report/): findings from the corpus",
          f"- [Dataset]({base}/dataset/): schema, access, licensing",
          f"- [Methodology]({base}/methodology/): sourcing rules, verification, QA",
          f"- [Free dataset sample]({base}/data/sample.jsonl): published records as JSONL",
          "", "## Categories", ""]
    lt += [f"- [{c}]({base}/category/{slugify(c)}/): {len(rs)} APIs" for c, rs in cats_sorted]
    lt += ["", "## Records", ""]
    lt += [f"- [{r.get('name') or r['domain']}]({base}/api/{r['domain']}/): "
           f"{(r.get('what_it_does') or '')[:110]}" for r in published]
    (DIST / "llms.txt").write_text("\n".join(lt) + "\n")

    # index + sponsors + correct + dataset + categories-index + 4 collections + methodology
    n_pages = 10 + len(published) + len(cats_sorted)
    print(f"built {n_pages} pages -> {DIST}")
    print(f"  {len(published)} record pages ({n_indexable} indexable >={INDEX_MIN_FIELDS} fields; "
          f"{len(published) - n_indexable} noindexed sparse)")
    print(f"  {len(cats_sorted)} category pages: "
          + ", ".join(f"{c} ({len(rs)})" for c, rs in cats_sorted[:8]) + " …")
    print(f"  sitemap: {len(urls)} urls · llms.txt · sample.jsonl ({len(published)} records)")


if __name__ == "__main__":
    main()
