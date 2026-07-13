#!/usr/bin/env python3
"""Manually add high-value domains the seed lists missed (gold-audit coverage gap,
2026-07-13). Probes llms.txt + /openapi.json like classify.py, appends to
seed_classified.jsonl + extract_queue.jsonl (source: "manual"), skips existing.
Zero deps. Usage: add_domains.py  (edit ADDITIONS below)
"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASSIFIED = ROOT / "data" / "seed_classified.jsonl"
QUEUE = ROOT / "data" / "extract_queue.jsonl"

# Modern-stack additions (2026-07-13): the APIs a funded startup / HN reader actually
# integrates, which the apis.guru + public-apis seed lists missed. 91 domains diffed
# against the existing queue. Names/descs/categories are seed hints only — fill
# re-derives what_it_does + category from evidence. The original coverage-gap 10
# (openai, anthropic, cloudflare, shopify, paypal, squareup, hubspot, algolia,
# supabase, openweathermap) are already queued; add_domains skips existing.
ADDITIONS = [
    # --- AI / LLM providers ---
    ("cohere.com", "Cohere API", "Enterprise LLMs: generation, embeddings, rerank, classify", "Machine Learning"),
    ("mistral.ai", "Mistral AI API", "Open-weight and frontier LLMs: chat, embeddings, agents", "Machine Learning"),
    ("perplexity.ai", "Perplexity API", "Web-grounded LLM answers via the Sonar API", "Machine Learning"),
    ("replicate.com", "Replicate API", "Run and fine-tune open-source ML models over REST", "Machine Learning"),
    ("deepgram.com", "Deepgram API", "Speech-to-text, text-to-speech and audio intelligence", "Machine Learning"),
    ("assemblyai.com", "AssemblyAI API", "Speech-to-text and audio understanding models", "Machine Learning"),
    ("together.ai", "Together AI API", "Serverless inference and fine-tuning for open models", "Machine Learning"),
    ("fireworks.ai", "Fireworks AI API", "Fast inference for open LLMs and image models", "Machine Learning"),
    ("openrouter.ai", "OpenRouter API", "Unified routing across many LLM providers", "Machine Learning"),
    ("stability.ai", "Stability AI API", "Image, video and 3D generative models", "Machine Learning"),
    ("deepl.com", "DeepL API", "Machine translation and text improvement", "Machine Learning"),
    ("x.ai", "xAI API", "Grok LLMs over an OpenAI-compatible REST API", "Machine Learning"),
    ("voyageai.com", "Voyage AI API", "Embedding and reranking models for retrieval", "Machine Learning"),
    # --- Vector DB / RAG / agent infra ---
    ("weaviate.io", "Weaviate API", "Open-source vector database with hybrid search", "Development"),
    ("qdrant.tech", "Qdrant API", "Vector similarity search engine and cloud", "Development"),
    ("trychroma.com", "Chroma API", "Open-source embedding database for AI apps", "Development"),
    ("langchain.com", "LangSmith API", "Tracing, evals and observability for LLM apps", "Development"),
    ("llamaindex.ai", "LlamaCloud API", "Parsing, indexing and retrieval for LLM apps", "Development"),
    ("browserbase.com", "Browserbase API", "Headless browser infrastructure for agents", "Development"),
    # --- Web data / scraping / search ---
    ("firecrawl.dev", "Firecrawl API", "Crawl and convert websites to LLM-ready markdown", "Data & Enrichment"),
    ("apify.com", "Apify API", "Web scraping and browser-automation actors", "Data & Enrichment"),
    ("exa.ai", "Exa API", "Neural web search built for AI", "Data & Enrichment"),
    ("tavily.com", "Tavily API", "Search API optimized for LLM agents", "Data & Enrichment"),
    ("serpapi.com", "SerpApi", "Real-time search-engine results scraping API", "Data & Enrichment"),
    ("scrapingbee.com", "ScrapingBee API", "Web scraping with headless browsers and proxies", "Data & Enrichment"),
    ("brightdata.com", "Bright Data API", "Proxy network and web data collection", "Data & Enrichment"),
    ("browserless.io", "Browserless API", "Hosted headless Chrome for scraping/automation", "Data & Enrichment"),
    ("scrapfly.io", "Scrapfly API", "Web scraping API with anti-bot bypass", "Data & Enrichment"),
    # --- Auth / identity ---
    ("clerk.com", "Clerk API", "Authentication, user and organization management", "Security & Auth"),
    ("workos.com", "WorkOS API", "Enterprise SSO, SCIM directory sync, audit logs", "Security & Auth"),
    ("okta.com", "Okta API", "Identity, SSO and user lifecycle management", "Security & Auth"),
    ("supertokens.com", "SuperTokens API", "Open-source auth and session management", "Security & Auth"),
    ("kinde.com", "Kinde API", "Auth, user management and feature flags", "Security & Auth"),
    ("descope.com", "Descope API", "Passwordless auth and user-journey flows", "Security & Auth"),
    # --- Payments / billing ---
    ("lemonsqueezy.com", "Lemon Squeezy API", "Merchant-of-record payments and subscriptions", "Payments"),
    ("paddle.com", "Paddle API", "Merchant-of-record billing for SaaS", "Payments"),
    ("braintreepayments.com", "Braintree API", "Card and wallet payment gateway", "Payments"),
    ("mollie.com", "Mollie API", "European payments and subscriptions", "Payments"),
    ("wise.com", "Wise Platform API", "Cross-border transfers and multi-currency accounts", "Payments"),
    ("gocardless.com", "GoCardless API", "Bank-debit and recurring payment collection", "Payments"),
    # --- Comms / email / notifications ---
    ("resend.com", "Resend API", "Transactional email for developers", "Communication"),
    ("mailgun.com", "Mailgun API", "Transactional and bulk email sending", "Communication"),
    ("loops.so", "Loops API", "Transactional and marketing email for SaaS", "Communication"),
    ("courier.com", "Courier API", "Multi-channel notification orchestration", "Communication"),
    ("knock.app", "Knock API", "Notifications infrastructure (in-app, email, push)", "Communication"),
    ("sendbird.com", "Sendbird API", "In-app chat, calls and messaging", "Communication"),
    ("messagebird.com", "Bird API", "SMS, voice and omnichannel messaging", "Communication"),
    ("novu.co", "Novu API", "Open-source notification infrastructure", "Communication"),
    ("plivo.com", "Plivo API", "SMS and voice communications", "Communication"),
    # --- Databases / data platforms ---
    ("neon.tech", "Neon API", "Serverless Postgres with branching", "Development"),
    ("planetscale.com", "PlanetScale API", "Serverless MySQL platform", "Development"),
    ("turso.tech", "Turso API", "Edge-hosted SQLite (libSQL) database", "Development"),
    ("upstash.com", "Upstash API", "Serverless Redis, Kafka and vector", "Development"),
    ("mongodb.com", "MongoDB Atlas API", "Managed MongoDB: data and admin APIs", "Development"),
    ("fauna.com", "Fauna API", "Distributed document-relational database", "Development"),
    ("xata.io", "Xata API", "Serverless Postgres with search and file storage", "Development"),
    ("cockroachlabs.com", "CockroachDB Cloud API", "Distributed SQL database management", "Development"),
    ("tinybird.co", "Tinybird API", "Real-time analytics on ClickHouse via API", "Development"),
    # --- Infra / hosting / deploy ---
    ("fly.io", "Fly.io Machines API", "Deploy containers and VMs near users", "Cloud & Infrastructure"),
    ("render.com", "Render API", "App and service deployment platform", "Cloud & Infrastructure"),
    ("railway.app", "Railway API", "Deploy apps, databases and services", "Cloud & Infrastructure"),
    ("modal.com", "Modal API", "Serverless compute for AI and batch jobs", "Cloud & Infrastructure"),
    ("scaleway.com", "Scaleway API", "European cloud: compute, storage, managed services", "Cloud & Infrastructure"),
    ("bunny.net", "Bunny.net API", "CDN, edge storage and video streaming", "Cloud & Infrastructure"),
    # --- Observability / dev tools ---
    ("sentry.io", "Sentry API", "Error and performance monitoring", "Developer Tools"),
    ("datadoghq.com", "Datadog API", "Metrics, logs, traces and monitoring", "Developer Tools"),
    ("linear.app", "Linear API", "Issue tracking and project management (GraphQL)", "Developer Tools"),
    ("figma.com", "Figma API", "Read and manage design files and assets", "Developer Tools"),
    ("statsig.com", "Statsig API", "Feature flags and experimentation", "Developer Tools"),
    ("honeycomb.io", "Honeycomb API", "Observability for distributed systems", "Developer Tools"),
    ("bugsnag.com", "BugSnag API", "Application error and stability monitoring", "Developer Tools"),
    # --- Product analytics ---
    ("posthog.com", "PostHog API", "Product analytics, flags and session replay", "Marketing"),
    ("mixpanel.com", "Mixpanel API", "Product analytics event tracking and export", "Marketing"),
    ("amplitude.com", "Amplitude API", "Product analytics and behavioral data", "Marketing"),
    ("segment.com", "Segment API", "Customer data pipeline and event routing", "Marketing"),
    # --- Search / CMS / content ---
    ("typesense.org", "Typesense API", "Open-source typo-tolerant search", "Development"),
    ("elastic.co", "Elasticsearch API", "Search and analytics engine", "Development"),
    ("sanity.io", "Sanity API", "Headless content platform queried with GROQ", "Media & Content"),
    ("strapi.io", "Strapi API", "Open-source headless CMS", "Media & Content"),
    ("storyblok.com", "Storyblok API", "Headless CMS with visual editor", "Media & Content"),
    ("prismic.io", "Prismic API", "Headless CMS and page builder", "Media & Content"),
    # --- Media / video ---
    ("mux.com", "Mux API", "Video encoding, streaming and analytics", "Media & Content"),
    ("cloudinary.com", "Cloudinary API", "Image and video upload, transform and delivery", "Media & Content"),
    ("imgix.com", "imgix API", "Real-time image processing and CDN", "Media & Content"),
    ("livekit.io", "LiveKit API", "Realtime audio/video (WebRTC) infrastructure", "Communication"),
    ("daily.co", "Daily API", "Video and audio calling APIs (WebRTC)", "Communication"),
    ("agora.io", "Agora API", "Real-time voice, video and streaming SDKs", "Communication"),
    # --- Scheduling / support SaaS ---
    ("calendly.com", "Calendly API", "Scheduling links, events and availability", "Productivity"),
    ("cal.com", "Cal.com API", "Open-source scheduling infrastructure", "Productivity"),
    ("intercom.com", "Intercom API", "Customer messaging, help desk and contacts", "Communication"),
    ("zendesk.com", "Zendesk API", "Support tickets, help center and CRM", "Communication"),
]


def probe(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "apiterms-census/1.0 (+https://apiterms.com)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200 and len(r.read(2048)) > 100:
                return url
    except Exception:
        pass
    return None


def main():
    have = {json.loads(l)["domain"] for l in QUEUE.open()}
    added = 0
    for dom, name, desc, cat in ADDITIONS:
        if dom in have:
            print(f"{dom}: already queued, skip")
            continue
        rec = {
            "domain": dom, "name": name, "description": desc, "category": cat,
            "auth_hint": None, "spec_url": None, "spec_count": 0,
            "sources": ["manual"], "alive": True,
            "llms_txt": probe(f"https://{dom}/llms.txt"),
            "openapi_probe": probe(f"https://{dom}/openapi.json"),
            "queue_rank": 9999,
        }
        with CLASSIFIED.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{dom}: added (llms.txt={'yes' if rec['llms_txt'] else 'no'})")
        added += 1
    print(f"done: {added} added")


if __name__ == "__main__":
    main()
