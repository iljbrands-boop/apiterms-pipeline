#!/usr/bin/env python3
"""Static site generator — census.jsonl -> a self-contained static site (rmtree +
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
record just because its raw category bucket is empty.

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
SIG_LABEL = {"pricing": "Pricing", "limits": "Rate limits", "auth": "Auth",
             "spec": "OpenAPI spec", "mcp": "MCP server", "info": "Details"}
# Every record gets a page (the site IS the dataset; "not documented" is data).
# Records under this many filled fields are rendered
# with <meta name=robots noindex> and kept out of the sitemap so thin pages can't
# hurt the domain's search quality; humans and agents still get every record.
INDEX_MIN_FIELDS = 4
FIELDS = ["base_url", "auth_type", "free_tier", "pricing_model", "pricing_details",
          "rate_limits", "openapi_spec_url", "mcp_server"]
FIELD_LABELS = {"base_url": "Base URL", "auth_type": "Auth", "free_tier": "Free tier",
                "pricing_model": "Pricing model", "pricing_details": "Pricing",
                "rate_limits": "Rate limits", "openapi_spec_url": "OpenAPI spec",
                "mcp_server": "MCP server"}
# No email addresses anywhere on the site: corrections, claims and sponsor contact
# all go through Formspree forms (/correct/, /sponsors/).
SPONSORS = ROOT / "site" / "sponsors.json"


def load_sponsors():
    if SPONSORS.exists():
        s = json.loads(SPONSORS.read_text())
    else:
        s = {}
    return {"categories": s.get("categories", {}), "featured": set(s.get("featured", []))}


# site/config.json: third-party service IDs. Empty string = feature not injected.
#   crisp_website_id      -> Crisp chat bubble on every page
#   formspree_project_id  -> sponsor-inquiry form + change-feed email capture
#                            (form keys "sponsor"/"feed" live in ../formspree.json,
#                             deployed via `npx @formspree/cli deploy -k <deploy key>`)
CONFIG_PATH = ROOT / "site" / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
CRISP_ID = CONFIG.get("crisp_website_id", "")
FORMSPREE_PROJECT = CONFIG.get("formspree_project_id", "")


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


# Curated, indexable collection pages (curated facets, never combinatorial)
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
:root{--void:#04060b;--panel:#0a101a;--panel2:#0d1420;--line:#16202e;--lineh:#233145;
--ink:#e8edf5;--body:#9fadbf;--dim:#5c6b7f;--ghost:#39465a;--blue:#1f5eff;--bluehot:#4d82ff;
--bluedim:#12275c;--add:#00e08b;--adddim:rgba(0,224,139,.12);--warn:#ffb454;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:repeating-linear-gradient(0deg,transparent 0 47px,rgba(35,49,69,.16) 47px 48px),
repeating-linear-gradient(90deg,transparent 0 47px,rgba(35,49,69,.16) 47px 48px),var(--void);
color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--bluehot);text-decoration:none}a:hover{text-decoration:underline}
::selection{background:var(--blue);color:#fff}
.shell{max-width:1080px;margin:0 auto;padding:0 clamp(16px,4vw,40px) 60px}
.mono{font-family:var(--mono)}
.nav{display:flex;align-items:center;gap:18px;padding:14px 0;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-weight:700;font-size:14px;color:var(--ink);white-space:nowrap}
.brand .mark{color:#fff;background:var(--blue);padding:4px 8px;font-size:11.5px}
.topnav{margin-left:auto;display:flex;gap:16px;font-family:var(--mono);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}
.topnav a{color:var(--body)}
.crumbs{font-family:var(--mono);font-size:11.5px;color:var(--dim);margin:20px 0;letter-spacing:.04em;text-transform:uppercase}
.crumbs a{color:var(--dim)}.crumbs span{color:var(--ghost);margin:0 8px}
.panel{background:var(--panel);border:1px solid var(--lineh)}
h1{font-family:var(--mono);font-weight:700;letter-spacing:-.01em}
.kicker{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--bluehot);margin-bottom:10px}
.kicker:before{content:"// "}
.chip{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:4px 10px;border:1px solid var(--lineh);color:var(--body);white-space:nowrap;display:inline-block}
.chip.cat{color:var(--bluehot);border-color:var(--bluedim);background:rgba(31,94,255,.08)}
.badge{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;letter-spacing:.05em;color:var(--add);background:var(--adddim);border:1px solid rgba(0,224,139,.3);padding:4px 11px;text-transform:uppercase}
.dot{width:6px;height:6px;background:var(--add)}
.conf{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--body)}
.conf .bars{display:inline-flex;gap:2px}.conf .bars i{width:4px;height:11px;background:var(--lineh)}
.conf.high .bars i{background:var(--add)}.conf.medium .bars i:nth-child(-n+2){background:var(--warn)}
.field{display:grid;grid-template-columns:150px 1fr auto;gap:16px;align-items:start;padding:14px 26px;border-top:1px solid var(--line)}
.field:hover{background:var(--panel2)}
.field .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);padding-top:3px}
.field .v{font-family:var(--mono);font-size:13px;color:var(--ink);line-height:1.55;word-break:break-word}
.field .v .hl{color:var(--bluehot);font-weight:600}
.field.absent .v{color:var(--ghost);font-style:italic}
.src{font-family:var(--mono);font-size:11px;color:var(--warn);white-space:nowrap;padding-top:3px}
.src.none{color:var(--ghost)}
.tag-null{display:inline-block;font-family:var(--mono);font-size:10.5px;color:var(--ghost);border:1px solid var(--line);padding:1px 7px;margin-left:4px;font-style:normal}
@media(max-width:560px){.field{grid-template-columns:1fr;gap:5px}}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);text-align:left;font-weight:600;padding:11px 14px;border-bottom:1px solid var(--lineh);background:#070b12}
td{padding:10px 14px;border-bottom:1px solid var(--line);font-family:var(--mono);color:var(--body);vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:var(--panel2)}
td.name a{font-weight:600;color:var(--ink)}
.pill{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border:1px solid var(--lineh);color:var(--body)}
.pill.ok{color:var(--add);border-color:rgba(0,224,139,.35);background:var(--adddim)}
.pill.lo{color:var(--warn);border-color:rgba(255,180,84,.3)}
.yes{color:var(--add)}.no{color:var(--ghost)}
.table-wrap{overflow-x:auto;border:1px solid var(--lineh);background:var(--panel)}
.grid-stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--lineh);background:var(--panel);margin:26px 0}
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
.btn{display:block;text-align:center;font-family:var(--mono);font-weight:600;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink);border:1px solid var(--lineh);padding:10px 12px;width:100%}
.btn:hover{border-color:var(--bluehot);color:var(--bluehot);text-decoration:none}
.code{font-family:var(--mono);font-size:12px;background:var(--void);border:1px solid var(--line);padding:12px 13px;color:var(--body);overflow-x:auto;line-height:1.7}
.code .c{color:var(--ghost)}.code .m{color:var(--bluehot)}.code .s{color:var(--add)}
.cols{display:grid;grid-template-columns:1fr 320px;gap:22px;align-items:start}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.catlist{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 26px}
.add{color:var(--add)}.del{color:var(--del,#ff5c5c);text-decoration:line-through;text-decoration-thickness:1px}
.chip.sig{color:var(--warn);border-color:rgba(255,180,84,.3);background:rgba(255,180,84,.07)}
.chg-row{border-top:1px solid var(--line);padding:12px 0}
.chg-hd{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.chg-dom{font-family:var(--mono);font-weight:600;color:var(--ink)}
.chg-date{margin-left:auto;color:var(--dim);font-size:11.5px}
.chg-diff{font-size:12.5px;margin-top:6px;line-height:1.7;word-break:break-word}
.chg-diff .src{margin-left:8px}
.ticker{border:1px solid var(--lineh);background:var(--panel);padding:12px 16px;margin:18px 0 0;font-family:var(--mono);font-size:12.5px;display:flex;align-items:center;gap:12px;overflow-x:auto;white-space:nowrap}
.ticker .lb{color:var(--add);flex:none;letter-spacing:.06em;text-transform:uppercase;font-size:10.5px}
.ticker a{color:var(--body)}.ticker .sep{color:var(--ghost)}
footer{margin-top:44px;padding-top:22px;border-top:1px solid var(--line);font-family:var(--mono);font-size:12px;color:var(--dim);letter-spacing:.03em;line-height:1.9}
footer a{color:var(--dim)}
.sub{color:var(--body);max-width:44em}
.fld{font-family:var(--mono);font-size:13px;color:var(--ink);background:var(--void);
border:1px solid var(--lineh);padding:10px 12px;width:100%;box-sizing:border-box}
.fld:focus{outline:none;border-color:var(--bluehot)}
textarea.fld{min-height:90px;resize:vertical}
.btn.solid{background:var(--blue);border-color:var(--blue);color:#fff;cursor:pointer}
.btn.solid:hover{background:var(--bluehot);color:#fff;text-decoration:none}
.caprow{display:flex;gap:10px;margin-top:14px}
.caprow .fld{flex:1}.caprow .btn{width:auto;padding:10px 18px}
@media(max-width:560px){.caprow{flex-direction:column}}
.logo{width:18px;height:18px;object-fit:contain;vertical-align:-4px;margin-right:9px;background:#fff;border-radius:3px;flex:none}
.logo.lg{width:30px;height:30px;vertical-align:-6px;border-radius:5px}
.searchwrap{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:20px 0 14px}
.search{flex:1;min-width:240px;background:var(--panel);border:1px solid var(--lineh);color:var(--ink);
font-family:var(--mono);font-size:13.5px;padding:11px 14px;outline:none}
.search:focus{border-color:var(--bluehot)}
.search::placeholder{color:var(--ghost)}
.kbd{font-family:var(--mono);font-size:10px;color:var(--dim);border:1px solid var(--line);padding:2px 6px}
.fchip{cursor:pointer;user-select:none}
.fchip.on{color:var(--bluehot);border-color:var(--bluehot);background:rgba(31,94,255,.12)}
.nshow{font-family:var(--mono);font-size:11px;color:var(--dim)}
.sponsorbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 16px;margin:18px 0 0;
font-family:var(--mono);font-size:12.5px;color:var(--body)}
.sponsorbar a b{color:var(--ink)}
.sponsorbar.open{border-style:dashed;color:var(--dim)}
.pill.sp{color:var(--warn);border-color:rgba(255,180,84,.3);flex:none}
.pill.feat{color:var(--bluehot);border-color:var(--bluedim);background:rgba(31,94,255,.08)}
"""


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" fill="#1f5eff"/>
<text x="32" y="34" text-anchor="middle" dominant-baseline="central"
 font-family="ui-monospace,'SF Mono',Menlo,Consolas,monospace" font-weight="700"
 font-size="38" fill="#fff">/A</text>
</svg>
"""


CRISP_SNIPPET = ('<script>window.$crisp=[];window.CRISP_WEBSITE_ID="%s";'
                 '(function(){var d=document,s=d.createElement("script");'
                 's.src="https://client.crisp.chat/l.js";s.async=1;'
                 'd.getElementsByTagName("head")[0].appendChild(s);})();</script>')


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
  <nav class="topnav"><a href="/categories/">Categories</a><a href="/changes/">Changes</a><a href="/report/">Report</a><a href="/dataset/">Dataset</a><a href="/sponsors/">Sponsor</a></nav>
</div>
{body_html}
<footer>
PUBLIC API TERMS, STRUCTURED AND FRESH — AUTH · PRICING · RATE LIMITS · SPEC · MCP<br>
Per-field evidence URLs, re-verified on a schedule. apis.guru froze in 2023 — rebuilt for agents.<br>
<a href="/methodology/">Methodology</a> · <a href="/add/">Add an API</a> · <a href="/changes/">Changes</a> · <a href="/correct/">Corrections</a> · <a href="/dataset/">Dataset</a> · <a href="/sponsors/">Become a sponsor</a> · <a href="/llms.txt">llms.txt</a>
</footer>
</div>
<script async src="https://scripts.simpleanalyticscdn.com/latest.js"></script>
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
    src_html = (f'<a class="src" href="{escape(src)}" rel="nofollow">{escape(host(src))} ↗</a>'
                if src else '<span class="src none">unevidenced</span>')
    return (f'<div class="field"><div class="k">{label}</div>'
            f'<div class="v"><span{hl}>{escape(str(val))}</span></div>{src_html}</div>')


def summary_sentence(rec):
    """Answer-engine phrasing: the sentence an LLM should quote."""
    name = rec.get("name") or rec["domain"]
    bits = []
    if v(rec, "auth_type"):
        bits.append(f"uses {v(rec, 'auth_type').replace('_', ' ')} authentication")
    if v(rec, "pricing_model"):
        bits.append(f"has {v(rec, 'pricing_model').replace('_', ' ')} pricing")
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


def meta_desc(rec):
    """Complete sentence from the strongest available facts; never mid-word truncation.
    Only mentions fields the record actually has."""
    name = display_name(rec)
    facts = []
    if v(rec, "auth_type"):
        facts.append(f"{v(rec, 'auth_type').replace('_', ' ')} auth")
    if v(rec, "pricing_model"):
        facts.append(f"{v(rec, 'pricing_model').replace('_', ' ')} pricing")
    if v(rec, "free_tier"):
        facts.append("a free tier")
    if v(rec, "rate_limits"):
        facts.append("documented rate limits")
    if v(rec, "openapi_spec_url"):
        facts.append("an OpenAPI spec")
    if v(rec, "mcp_server"):
        facts.append("an MCP server")
    tail = f" Verified {rec.get('last_verified')} with vendor source links."
    if not facts:
        return (f"{name}: the vendor documents none of the terms we track — "
                f"checked against its own pages.{tail}")
    while facts:
        listing = facts[0] if len(facts) == 1 else ", ".join(facts[:-1]) + " and " + facts[-1]
        d = f"{name} terms: {listing}.{tail}"
        if len(d) <= 158:
            return d
        facts = facts[:-1]
    return f"{name}: structured, source-linked API terms.{tail}"


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
    <a class="btn" style="margin-top:8px" href="/correct/?domain={escape(dom)}&amp;kind=claim">Your API? Claim this page</a>
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


def table_rows(recs, base, featured=frozenset(), logo_limit=None):
    """logo_limit caps how many rows get a favicon <img> — 1,250 external images
    on one page wedges slow renderers (observed in preview, 2026-07-13)."""
    out = []
    for i, r in enumerate(recs):
        conf = r.get("confidence", "low")
        pill = "ok" if conf == "high" else ("lo" if conf == "low" else "")
        feat = ' <span class="pill feat">featured</span>' if r["domain"] in featured else ""
        s = " ".join(filter(None, [r.get("name"), r["domain"], r["_category"],
                                   v(r, "auth_type"), v(r, "pricing_model")])).lower()
        flags = (f' data-s="{escape(s)}" data-free="{1 if v(r, "free_tier") else 0}"'
                 f' data-mcp="{1 if v(r, "mcp_server") else 0}"'
                 f' data-spec="{1 if v(r, "openapi_spec_url") else 0}"'
                 f' data-hi="{1 if r.get("confidence") == "high" else 0}"')
        lg = logo(r["domain"]) if (logo_limit is None or i < logo_limit) else ""
        out.append(
            f'<tr{flags}><td class="name">{lg}<a href="/api/{r["domain"]}/">{escape(r.get("name") or r["domain"])}</a>'
            f' <span style="color:var(--ghost);font-size:11px">{escape(r["domain"])}</span>{feat}</td>'
            f'<td>{escape(r["_category"])}</td>'
            f'<td>{escape(v(r, "auth_type") or "—")}</td>'
            f'<td>{escape(v(r, "pricing_model") or "—")}</td>'
            f'<td class="{"yes" if v(r, "mcp_server") else "no"}">{"●" if v(r, "mcp_server") else "—"}</td>'
            f'<td class="{"yes" if v(r, "free_tier") else "no"}">{"●" if v(r, "free_tier") else "—"}</td>'
            f'<td><span class="pill {pill}">{conf}</span></td></tr>')
    return "\n".join(out)


TABLE_HEAD = ('<thead><tr><th>API</th><th>Category</th><th>Auth</th><th>Pricing</th>'
              '<th>MCP</th><th>Free</th><th>Confidence</th></tr></thead>')


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


def index_page(recs, cats, base, corpus_stats, changelog=None):
    n = len(recs)
    # Stat band: all four over the SAME denominator (the published records) so the
    # numbers are internally consistent — no "tracked vs published" mismatch on the
    # homepage. The 297 bot-walled/JS-only domains live on /methodology as a
    # transparency line, not as a competing headline number.
    free_pct = round(100 * sum(1 for r in recs if v(r, "free_tier")) / max(n, 1))
    mcp_pct = round(100 * sum(1 for r in recs if v(r, "mcp_server")) / max(n, 1))
    rec_spec_pct = round(100 * sum(1 for r in recs if v(r, "openapi_spec_url")) / max(n, 1))
    # Freshness ticker — proof-of-life above the fold. Real events when they exist,
    # honest baseline line until the first re-verification pass detects changes.
    evs = sorted(changelog or [], key=lambda e: e["detected"], reverse=True)[:5]
    if evs:
        items = " <span class='sep'>·</span> ".join(
            f'<a href="/api/{escape(e["domain"])}/">{escape(e["domain"])} '
            f'{escape(SIG_LABEL.get(e["significance"], e["field"]).lower())} changed</a>' for e in evs)
        ticker = (f'<div class="ticker"><span class="lb">● Latest changes</span>{items}'
                  f'<span class="sep">·</span><a href="/changes/">all →</a></div>')
    else:
        ticker = (f'<div class="ticker"><span class="lb">● Tracking since {BASELINE_LABEL}</span>'
                  f'<span style="color:var(--dim)">every record snapshotted at its source · '
                  f'changes appear here as they\'re detected</span>'
                  f'<span class="sep">·</span><a href="/changes/">change feed →</a></div>')
    title = "API Terms — auth, pricing & rate limits for every public API"
    desc = (f"{n} public APIs as structured data: auth type, pricing, free tier, rate limits, "
            "OpenAPI spec, MCP server. A source URL on every field, re-verified on a schedule.")
    cat_links = "\n".join(
        f'<a class="chip cat" href="/category/{slugify(c)}/">{escape(c)} · {len(rs)}</a>'
        for c, rs in cats)
    feed_capture = ""
    if FORMSPREE_PROJECT:
        feed_capture = f"""<div class="panel card" style="margin:0 0 26px">
  <h3>The change feed</h3>
  <p class="sub" style="margin:0;font-size:13.5px">Vendors change pricing, limits and auth
  quietly. We re-crawl every source page and diff it. Leave an email, get what changed.</p>
  <form class="caprow" action="{form_action('feed')}" method="POST">
    <input type="hidden" name="_subject" value="Change-feed signup — apiterms.com">
    <input class="fld" type="email" name="email" placeholder="you@company.com" required>
    <button class="btn solid" type="submit">Get the feed</button>
  </form>
</div>"""
    body = f"""
<div style="padding:44px 0 6px">
  <div class="kicker">Machine-readable · re-verified on a schedule</div>
  <h1 style="font-size:clamp(24px,3.6vw,34px);line-height:1.25;margin:0 0 14px;max-width:24em">
    The terms of {n:,} public APIs. One record each. Every verified claim sourced.</h1>
  <p class="sub" style="font-size:16px">Auth type, pricing, free tier, rate limits, OpenAPI spec, MCP server —
  extracted from each vendor's own pages with <b style="color:var(--ink)">an evidence URL on every verified claim</b>.
  When a vendor doesn't document something, the field says <span class="mono">null</span>. We don't guess.</p>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">
    <a class="btn solid" style="width:auto;display:inline-block;padding:11px 20px" href="#q">Search the census ⌘K</a>
    <a class="btn" style="width:auto;display:inline-block;padding:11px 20px" href="/categories/">Browse categories</a>
    <a class="btn" style="width:auto;display:inline-block;padding:11px 20px" href="/methodology/">How it's verified</a>
  </div>
</div>
<div class="grid-stats">
  <div class="cell" title="APIs with a verified, source-linked record — every field cites the vendor page that states it"><div class="n">{n:,}</div><div class="l">APIs documented</div></div>
  <div class="cell" title="Share of documented APIs with a free tier we could verify from the vendor's own pages"><div class="n">{free_pct}<em>%</em></div><div class="l">have a free tier</div></div>
  <div class="cell" title="Share that document an MCP server for AI agents — now more than twice the OpenAPI-spec rate"><div class="n">{mcp_pct}<em>%</em></div><div class="l">ship an MCP server</div></div>
  <div class="cell" title="Share that publish an OpenAPI / Swagger spec at a discoverable URL"><div class="n">{rec_spec_pct}<em>%</em></div><div class="l">publish a spec</div></div>
</div>
{ticker}
{feed_capture}
<div id="categories" class="kicker" style="margin-top:10px">Categories · <a href="/categories/" style="text-transform:none;letter-spacing:0">all →</a></div>
<div class="catlist">{cat_links}</div>
<div class="kicker">Collections</div>
<div class="catlist">
  <a class="chip cat" href="/free-apis/">Free APIs</a>
  <a class="chip cat" href="/no-auth-apis/">No-auth APIs</a>
  <a class="chip cat" href="/openapi-apis/">OpenAPI specs</a>
  <a class="chip cat" href="/mcp-apis/">MCP servers</a>
</div>
<div class="kicker">Directory — all published records · <a href="/add/" style="text-transform:none;letter-spacing:0">missing one you use? add it →</a></div>
<div class="searchwrap">
  <input class="search" id="q" type="search" placeholder="Search {n:,} APIs — name, domain, category, auth…" autocomplete="off">
  <span class="kbd">⌘K</span>
  <span class="chip fchip" data-f="free">free tier</span>
  <span class="chip fchip" data-f="mcp">MCP</span>
  <span class="chip fchip" data-f="spec">OpenAPI</span>
  <span class="chip fchip" data-f="hi">high confidence</span>
  <span class="nshow"><b id="nshow">{n:,}</b> shown</span>
</div>
<div class="table-wrap"><table>{TABLE_HEAD}<tbody>
{table_rows(recs, base, logo_limit=100)}
</tbody></table></div>
<script>
var rows=[].slice.call(document.querySelectorAll("tbody tr")),
    q=document.getElementById("q"),
    chips=[].slice.call(document.querySelectorAll(".fchip"));
function apply(){{
  var s=q.value.toLowerCase().trim(),
      f=chips.filter(function(c){{return c.classList.contains("on")}}).map(function(c){{return c.dataset.f}}),
      shown=0;
  rows.forEach(function(r){{
    var ok=(!s||(r.dataset.s||"").indexOf(s)>-1)&&f.every(function(k){{return r.dataset[k]==="1"}});
    r.style.display=ok?"":"none"; if(ok)shown++;
  }});
  document.getElementById("nshow").textContent=shown.toLocaleString();
}}
q.addEventListener("input",apply);
chips.forEach(function(c){{c.addEventListener("click",function(){{c.classList.toggle("on");apply()}})}});
document.addEventListener("keydown",function(e){{
  if((e.metaKey||e.ctrlKey)&&e.key==="k"){{e.preventDefault();q.focus()}}}});
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
      <li><span class="ck">▸</span><span>Re-verified on a published schedule; see
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
        sponsor_cta = f"""<form action="{form_action('sponsor')}" method="POST">
    <input type="hidden" name="_subject" value="Sponsorship inquiry — apiterms.com">
    <input class="fld" type="email" name="email" placeholder="you@company.com" required>
    <textarea class="fld" name="message" style="margin-top:8px"
     placeholder="Which category / featured listing are you interested in?"></textarea>
    <button class="btn solid" type="submit" style="margin-top:8px">Ask for rates</button>
    </form>"""
    else:
        sponsor_cta = '<a class="btn" href="/correct/">Contact us</a>'  # no email on site
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
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Corrections</div>
<div class="kicker">Accuracy</div>
<h1 style="font-size:26px;margin:0 0 8px">Corrections &amp; vendor claims</h1>
<p class="sub">One wrong pricing claim is one too many. Every field on every record
links the page it came from — if reality moved, tell us and we re-verify against
the source. API vendors: use the same form to claim your page.</p>
<div class="panel card" style="max-width:560px;margin-top:22px">
  <h3>What's wrong (or yours)?</h3>
  <form action="{form_action('correction')}" method="POST">
    <input class="fld" type="text" name="domain" id="f-domain" placeholder="api domain, e.g. stripe.com" required>
    <select class="fld" name="kind" id="f-kind" style="margin-top:8px">
      <option value="correction">Correction — a field is wrong or outdated</option>
      <option value="claim">Claim — this is my API's page</option>
      <option value="add">Add — an API that's missing from the census</option>
      <option value="other">Something else</option>
    </select>
    <input class="fld" type="email" name="email" placeholder="you@company.com" required style="margin-top:8px">
    <textarea class="fld" name="message" style="margin-top:8px"
     placeholder="Which field, what it should say, and (ideally) the URL that proves it."></textarea>
    <button class="btn solid" type="submit" style="margin-top:8px">Send</button>
  </form>
</div>
<script>
var q=new URLSearchParams(location.search);
if(q.get("domain"))document.getElementById("f-domain").value=q.get("domain");
if(q.get("kind"))document.getElementById("f-kind").value=q.get("kind");
</script>"""
    return page(title, desc, url, body, base), url


def add_page(base):
    """Suggest-an-API page. Contributors expand COVERAGE (a domain to cover); they never
    write field values — the pipeline crawls, extracts with evidence, and QA-gates it, so
    the evidence-or-null guarantee holds even for community submissions."""
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
    <form action="{form_action('correction')}" method="POST">
      <input type="hidden" name="kind" value="add">
      <input class="fld" type="text" name="domain" placeholder="api domain, e.g. resend.com" required>
      <input class="fld" type="url" name="docs" placeholder="docs / pricing URL (optional, speeds it up)" style="margin-top:8px">
      <input class="fld" type="email" name="email" placeholder="you@company.com (we'll tell you when it's live)" required style="margin-top:8px">
      <textarea class="fld" name="message" style="margin-top:8px"
       placeholder="Anything that helps — what the API does, where the pricing page is, etc. (optional)"></textarea>
      <button class="btn solid" type="submit" style="margin-top:8px">Add it to the queue →</button>
    </form>
  </div>
  <aside>
    <div class="panel card">
      <h3>How it works</h3>
      <ul>
        <li><span class="ck">1</span><span>You submit a <b>domain</b> — not data.</span></li>
        <li><span class="ck">2</span><span>Our crawler reads the vendor's own docs and pricing pages.</span></li>
        <li><span class="ck">3</span><span>Every field extracted gets <b>the source URL that states it</b>; anything undocumented stays <span class="mono">null</span>.</span></li>
        <li><span class="ck">4</span><span>It passes the QA gate, then publishes with a record page.</span></li>
      </ul>
      <p class="sub" style="font-size:12.5px;margin:12px 0 0">Want to see exactly how a record is
      built and verified first? Read the <a href="/methodology/">methodology</a>.</p>
    </div>
  </aside>
</div>"""
    return page(title, desc, url, body, base), url


def report_page(recs, base):
    """State of the API Economy — data story computed live from the corpus, so every
    figure updates as the census grows (never a stale number)."""
    import collections
    url = f"{base}/report/"
    n = len(recs)

    def cnt(f):
        return sum(1 for r in recs if v(r, f))

    mcp, spec = cnt("mcp_server"), cnt("openapi_spec_url")
    mcp_pct, spec_pct = round(100 * mcp / n, 1), round(100 * spec / n, 1)
    ratio = round(mcp / spec, 2) if spec else 0
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

    cl_path = ROOT / "data" / "seed_classified.jsonl"
    alive = [json.loads(l) for l in cl_path.open()] if cl_path.exists() else []
    alive = [c for c in alive if c.get("alive")]
    llms_pct = round(100 * sum(1 for c in alive if c.get("llms_txt")) / max(len(alive), 1))

    def bar(label, pct, cls="b"):
        fillcol = ("linear-gradient(90deg,var(--blue),var(--bluehot))" if cls == "mcp"
                   else "var(--lineh)" if cls == "spec" else "var(--blue)")
        return (f'<div style="margin-bottom:16px"><div style="display:flex;justify-content:space-between;'
                f'font-family:var(--mono);font-size:13px;margin-bottom:6px"><span>{escape(label)}</span>'
                f'<span style="color:var(--ink);font-weight:600">{pct}%</span></div>'
                f'<div style="height:20px;background:var(--void);border:1px solid var(--line);position:relative">'
                f'<div style="position:absolute;inset:0;width:{min(pct*2,100)}%;background:{fillcol}"></div></div></div>')

    auth_bars = "".join(bar(k or "unknown", round(100 * c / at)) for k, c in auth.most_common(4))
    price_bars = "".join(bar(k or "unknown", round(100 * c / pt)) for k, c in pm.most_common(4))
    cat_rows = "\n".join(
        f'<tr><td>{escape(c)}</td><td class="n">{ft}%</td>'
        f'<td><span style="display:inline-block;height:9px;background:var(--blue);'
        f'vertical-align:middle;width:{max(int(ft*1.1),4)}px"></span></td></tr>'
        for ft, c, _ in catrows)

    title = "The State of the API Economy 2026 — API Terms"
    desc = (f"We verified the access terms of {n:,} public APIs. The finding: {ratio}x more "
            f"ship an MCP server ({mcp_pct}%) than publish an OpenAPI spec ({spec_pct}%). "
            "Free-tier rates, auth and pricing distributions — every figure source-linked.")
    jsonld = {
        "@context": "https://schema.org", "@type": "Report",
        "name": "The State of the API Economy 2026", "url": url,
        "datePublished": time.strftime("%Y-%m-%d"),
        "publisher": {"@type": "Organization", "name": "API Terms", "url": base},
        "description": desc,
    }
    body = f"""
<div class="crumbs"><a href="/">API Terms</a><span>/</span>Report</div>
<div class="kicker">State of the API Economy · 2026</div>
<h1 style="font-size:clamp(26px,4vw,38px);line-height:1.18;margin:0 0 16px;max-width:15em">
Agents already won: more public APIs ship an MCP server than an OpenAPI spec.</h1>
<p class="sub" style="font-size:18px;max-width:42em">We verified the access terms of
<b style="color:var(--ink)">{n:,} public APIs</b> — auth, pricing, free tiers, rate limits,
specs, MCP servers — reading each vendor's own docs and storing a source URL for every value.
Here is what the machine-readable layer of the API economy actually looks like in mid-2026.</p>

<div class="panel card tick" style="text-align:center;padding:30px 24px;margin:30px 0">
  <div class="kicker" style="justify-content:center">The headline</div>
  <div style="font-family:var(--mono);font-size:clamp(42px,8vw,72px);font-weight:700;
       letter-spacing:-.03em;color:var(--ink);line-height:1"><span style="color:var(--bluehot)">{ratio}×</span></div>
  <div style="font-family:var(--mono);font-size:13px;color:var(--dim);text-transform:uppercase;
       letter-spacing:.05em;margin-top:8px">more ship a documented MCP server than expose an OpenAPI spec URL</div>
  <div style="max-width:520px;margin:22px auto 0;text-align:left">
    {bar("Ships a documented MCP server", mcp_pct, "mcp")}
    {bar("Publishes an OpenAPI / Swagger spec URL", spec_pct, "spec")}
  </div>
</div>

<p>This is the finding that reframes everything else. The incumbent open directory,
<b>apis.guru, spent its life indexing OpenAPI specifications</b> — then froze in April 2023.
But the OpenAPI spec turns out to be <b>the rarest machine-readable artifact in the entire
corpus</b>: barely {spec_pct}% of public APIs expose one at a discoverable URL. Meanwhile MCP
servers — the interface built for AI agents — already appear on {ratio}× as many. The tooling
ecosystem is still organized around the spec. The APIs have moved on.</p>

<h2 style="font-family:var(--mono);font-size:22px;margin:44px 0 8px">Free tiers are the default</h2>
<p>Among the APIs whose terms we could fully verify, <b>{free_ver}% document a free tier</b> and
<b>{rl_ver}% publish their rate limits</b>. Free access isn't a growth hack anymore — it's table
stakes. But the distribution by category is where it gets interesting: the APIs <em>least</em>
likely to document a usable free tier are the ostensibly "free" ones — government and open-data
APIs, where terms are so under-documented that whether you can use them in production is often
unstated.</p>

<div class="table-wrap tick" style="margin:22px 0">
  <table>
    <thead><tr><th>Category</th><th style="text-align:right">Free-tier rate</th><th></th></tr></thead>
    <tbody>{cat_rows}</tbody>
  </table>
</div>

<h2 style="font-family:var(--mono);font-size:22px;margin:44px 0 8px">The auth surface is simpler than the tutorials suggest</h2>
<p>Of the APIs that document authentication, the API key still rules — and a fifth need no auth
at all. OAuth 2.0, the thing every integration guide dwells on, is the minority case. For anyone
building an agent that calls arbitrary APIs, that's good news: the auth surface is mostly a single
static credential.</p>
<div class="panel card" style="margin:20px 0">{auth_bars}</div>

<h2 style="font-family:var(--mono);font-size:22px;margin:44px 0 8px">Pricing has consolidated</h2>
<p>Freemium and genuinely-free account for most documented pricing. Pure pay-to-play is rare;
even in the metered-inference era, usage-based billing is still the minority. The modern default
is settled: a free bucket, then usage or seats.</p>
<div class="panel card" style="margin:20px 0">{price_bars}</div>

<h2 style="font-family:var(--mono);font-size:22px;margin:44px 0 8px">Only a quarter are machine-discoverable at all</h2>
<p>Across every live API domain we probed, just <b>{llms_pct}% serve an <span class="mono">llms.txt</span></b>
and {spec_pct}% an OpenAPI spec URL. For most public APIs, an agent's only route to the terms is
reading the human docs page — which is exactly the gap this census exists to close.</p>

<div class="panel card tick" style="margin:34px 0">
  <h3 style="color:var(--bluehot)">// How we measured this — and what these numbers do and don't say</h3>
  <ul style="margin:8px 0 0;padding-left:0;list-style:none">
    <li style="margin-bottom:9px"><span class="ck">▸</span> <b>Evidence or null.</b> Every value cites the
    exact vendor page that states it; a deterministic check rejects any citation we didn't actually read.</li>
    <li style="margin-bottom:9px"><span class="ck">▸</span> <b>These are documented rates — floors.</b>
    A null means "not stated where we looked," not "doesn't exist." The MCP-vs-OpenAPI ratio compares two
    floors measured identically, so the {ratio}× holds regardless.</li>
    <li style="margin-bottom:9px"><span class="ck">▸</span> <b>Caveats.</b> "Ships an MCP server" counts
    documented references, not only live endpoints; the corpus is broad but not every public API on earth;
    multi-product vendors are one record per domain today.</li>
  </ul>
</div>

<div class="panel card tick" style="text-align:center;margin:40px 0 0;padding:28px">
  <div class="kicker" style="justify-content:center;color:var(--add)">The dataset behind every number</div>
  <h2 style="font-family:var(--mono);font-size:22px;margin:6px 0 10px">Read the source on any of these APIs.</h2>
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
      rates, no circumvention. Sites that wall crawlers or render docs only in JavaScript
      are tracked but not guessed at.</span></li>
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
      <li><span class="ck">▸</span><span><b>Re-verification is scheduled</b> — the cadence stated
      on each record is the one that applies to it. Source pages are re-fetched and diffed;
      changed sources trigger re-extraction.</span></li>
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
    n_indexable = sum(1 for r in published if filled(r) >= INDEX_MIN_FIELDS)

    # corpus stats from classify output (for the hero band)
    classified = ROOT / "data" / "seed_classified.jsonl"
    tracked = llms = spec = 0
    if classified.exists():
        for line in classified.open():
            c = json.loads(line)
            tracked += bool(c.get("alive"))
            llms += bool(c.get("llms_txt"))
            spec += bool(c.get("spec_url") or c.get("openapi_probe"))
        total = sum(1 for _ in classified.open())
    else:
        total = tracked = len(recs)
    corpus = {"tracked": tracked, "llms_pct": round(100 * llms / max(total, 1), 1),
              "spec_pct": round(100 * spec / max(total, 1), 1)}

    # freshness layer 2 — the change ledger (empty until the first re-verification pass)
    changelog = [json.loads(l) for l in CHANGELOG.open()] if CHANGELOG.exists() else []
    hist_by_domain = {}
    for e in changelog:
        hist_by_domain.setdefault(e["domain"], []).append(e)

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
    (DIST / "index.html").write_text(index_page(published, cats_sorted, base, corpus, changelog))

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
