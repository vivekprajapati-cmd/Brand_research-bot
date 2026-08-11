# Brand Research Automation
## Product Requirements & Architecture Document

**Version:** 1.0 | **Date:** August 2026  
**Stack:** Slack → Gemini Flash Vision → Instaloader → DuckDuckGo → Google Sheets  
**Total Monthly Cost:** ₹0

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Stakeholders & Users](#2-stakeholders--users)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [System Architecture](#5-system-architecture)
6. [Project Structure](#6-project-structure)
7. [Environment Variables](#7-environment-variables)
8. [Data Model — Google Sheet Columns](#8-data-model--google-sheet-columns)
9. [Internal API Contracts](#9-internal-api-contracts)
10. [Testing Strategy](#10-testing-strategy)
11. [Dependencies](#11-dependencies-requirementstxt)
12. [Deployment Guide](#12-deployment-guide)
13. [Risks & Mitigations](#13-risks--mitigations)
14. [Future Scope](#14-future-scope-v2)

---

## 1. Project Overview

Brand Research Automation is a zero-cost Python backend that listens on a Slack channel. Whenever a marketing manager shares an Instagram brand post (as an image or screenshot), the system automatically:

- Detects and downloads the image from Slack
- Extracts brand identity details using Gemini Flash Vision (free tier)
- Scrapes the brand's public Instagram profile via Instaloader
- Researches the brand online using DuckDuckGo search
- Synthesises a structured research brief using Gemini Flash
- Appends a fully populated row to a Google Sheet — ready for outreach

### 1.1 Problem Statement

A marketing agency employee receives Instagram post images from their manager via Slack. For each brand they must manually visit Instagram, search the web, compile notes, and log everything before reaching out. This takes 20–40 minutes per brand and is entirely repetitive.

### 1.2 Goals

- Reduce per-brand research time from ~30 min to under 3 min
- Zero manual data entry into the tracking sheet
- 100% free infrastructure — no paid APIs, no paid hosting
- Fully automated — trigger-and-forget, no human steps between Slack post and Sheet row

### 1.3 Non-Goals

- Sending outreach emails or DMs automatically (out of scope v1)
- A custom web UI or dashboard — Google Sheet is the interface
- Support for TikTok, YouTube, or other platforms (v1 Instagram only)
- Real-time analytics or reporting

---

## 2. Stakeholders & Users

| Role | Description |
|---|---|
| **Primary User** | Marketing executive who receives posts and does outreach |
| **Trigger Actor** | Manager / boss who shares Instagram posts on Slack |
| **Data Consumer** | Same executive — reads the auto-populated Google Sheet |
| **System Owner** | Developer who deploys and maintains the bot on Render |

---

## 3. Functional Requirements

### FR-01 — Slack Event Listener

- Bot must join the designated Slack channel
- Must listen for `file_shared` and `message` events
- Must filter: only process image attachments (JPEG, PNG, WEBP)
- Must ignore messages from bots and the bot itself
- Must respond with a Slack thread reply acknowledging receipt

### FR-02 — Image Download

- Download the full-resolution image from Slack using the bot token
- Save to a local `/tmp` directory with a UUID filename
- Validate the file is a real image before processing
- Delete temp file after processing completes or fails

### FR-03 — Brand Extraction (AI Vision)

- Send image to Gemini 1.5 Flash (free tier) via Google AI Studio API
- Prompt must extract: brand name, Instagram handle, product/service type, tagline, visible contact info (email, phone, website)
- Return structured JSON — not free text
- If extraction confidence is low, flag the row with status `"Review Needed"`

### FR-04 — Instagram Profile Scrape

- Use Instaloader to fetch the public profile for the extracted handle
- Extract: full name, bio, follower count, following count, post count, website URL, business category, is_verified
- Handle private profiles gracefully — log `"Profile is private"`, continue
- Handle invalid handles — log `"Handle not found"`, continue
- Add a 2–5 second random delay between scrape requests to avoid rate limits

### FR-05 — Web Research

- Use `duckduckgo-search` Python library (free, no API key) to run 3 searches:
  - `"[brand name] company overview funding"`
  - `"[brand name] Instagram ads marketing agency"`
  - `"[brand name] contact email press"`
- Collect top 5 results per search (title + snippet + URL)
- Pass all snippets to Gemini Flash to generate a 150–200 word research brief

### FR-06 — Google Sheets Logging

- Append one row per brand to a configured Google Sheet
- Columns (in order): Timestamp, Brand Name, Instagram Handle, Niche/Category, Followers, Following, Posts, Website, Email, Phone, Bio, Is Verified, Research Notes, Source Post URL, Status
- Default Status value: `"To Contact"`
- If the same handle already exists in the sheet, update the row instead of duplicating
- Sheet ID and tab name must be configurable via environment variables

### FR-07 — Health Endpoint

- Expose `GET /health` as a **native FastAPI route** returning HTTP 200 and JSON `{"status": "ok"}`
- This is a plain FastAPI endpoint — completely independent of Slack Bolt
- Pinged by cron-job.org every 5 minutes to keep Render free tier awake
- Must respond within 500ms — no pipeline logic, just return immediately

### FR-08 — Error Handling & Notifications

- All pipeline errors must be caught — the bot must never crash silently
- On any failure, post an error message in the Slack thread of the triggering message
- Log all events (INFO/WARNING/ERROR) to stdout — Render captures these as logs

---

## 4. Non-Functional Requirements

| Requirement | Target |
|---|---|
| **Performance** | Full pipeline (image → Sheet row) must complete within 90 seconds |
| **Availability** | 99% uptime during business hours via Render + cron-job.org keepalive |
| **Cost** | Total monthly cost must be ₹0 — all free tiers only |
| **Scalability** | Handle up to 50 brands/day without rate limit issues |
| **Security** | All secrets stored as Render environment variables — never hardcoded |
| **Reliability** | Retry failed Gemini API calls up to 3 times with exponential backoff |
| **Observability** | Structured JSON logs for every pipeline stage |
| **Maintainability** | Each pipeline stage in its own module — independently testable |

---

## 5. System Architecture

### 5.1 High-Level Flow

```
[Slack Channel]
     │  boss shares Instagram post image
     ▼
[Slack Bolt App]  ←──── cron-job.org pings /health every 5 min
     │  file_shared event fires
     ▼
[Downloader]
     │  downloads image via Slack Files API
     ▼
[Vision Extractor]  ──→  Gemini 1.5 Flash API (free)
     │  returns structured JSON: brand name, handle, etc.
     ▼
[Instagram Scraper]  ──→  Instaloader (free, no API key)
     │  returns profile data: followers, bio, website, etc.
     ▼
[Web Researcher]  ──→  DuckDuckGo Search (free, no API key)
     │  returns 3×5 search snippets
     ▼
[Research Synthesiser]  ──→  Gemini 1.5 Flash API (free)
     │  returns 150-word research brief
     ▼
[Sheets Writer]  ──→  Google Sheets API (free)
     │  appends / updates one row
     ▼
[Slack Notifier]
     │  posts "✅ Done — check the Sheet" in thread
```

### 5.2 Deployment Architecture

| Component | Detail |
|---|---|
| **Hosting** | Render.com Free Tier — Python web service |
| **Keep-Alive** | cron-job.org — free HTTP ping every 5 minutes to `/health` |
| **Runtime** | Python 3.11 |
| **Web Framework** | FastAPI — outer server; Slack Bolt mounted via official adapter on `POST /slack/events` |
| **Web Server** | Uvicorn — runs FastAPI in production on Render |
| **`/health` Route** | Native FastAPI `GET` endpoint — clean, reliable, required for cron-job.org keepalive |
| **Slack Events** | Slack Bolt for Python — handles signature verification, event routing, retries |
| **Process Model** | Single async process — FastAPI + Bolt run together under Uvicorn |
| **Persistent State** | Google Sheets (no database needed) |
| **Secrets** | Render Environment Variables |
| **Logs** | Render Dashboard → Logs tab (stdout) |

### 5.3 FastAPI + Slack Bolt Integration

FastAPI is the outer web server. Slack Bolt runs inside it via the official `SlackRequestHandler` adapter. This gives you the best of both:

- **FastAPI** owns the server, routes, and `/health` endpoint natively
- **Slack Bolt** owns all Slack-specific logic: signature verification, event parsing, retries, and `ack()`
- **Uvicorn** runs the FastAPI app in production on Render

```python
# main.py — how FastAPI and Bolt wire together
from fastapi import FastAPI, Request
from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler

bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
handler  = SlackRequestHandler(bolt_app)
api      = FastAPI()

@api.post("/slack/events")   # Slack events → Bolt
async def slack_events(req: Request):
    return await handler.handle(req)

@api.get("/health")          # cron-job.org keepalive → FastAPI native
async def health():
    return {"status": "ok"}

# Start command on Render:
# uvicorn main:api --host 0.0.0.0 --port $PORT
```

> ✅ **Yes** — you absolutely access Slack Bolt through FastAPI. FastAPI receives the raw HTTP request and passes it to Bolt's handler. Bolt does all the Slack-specific processing internally.

### 5.4 External Service Dependency Map

| Service | Purpose |
|---|---|
| **Slack API** | Receive `file_shared` events, download images via `files.info`, post thread replies |
| **Google Gemini Flash** | Image analysis (vision) + research synthesis — free tier, no credit card |
| **Instaloader** | Public Instagram profile scraping — pure Python, no API key needed |
| **DuckDuckGo Search** | Web research queries — free Python library, no API key needed |
| **Google Sheets API** | Read/write brand research data — free via service account credentials |
| **cron-job.org** | Pings FastAPI `GET /health` every 5 min to prevent Render free tier sleep |

---

## 6. Project Structure

```
brand-research-bot/
├── main.py                   # Entry point — starts Slack Bolt app
├── config.py                 # Loads and validates all env vars
├── requirements.txt          # Python dependencies
├── render.yaml               # Render deployment config
├── .env.example              # Template for local env vars
│
├── pipeline/
│   ├── __init__.py
│   ├── downloader.py         # FR-02: Download image from Slack
│   ├── vision_extractor.py   # FR-03: Gemini vision → brand JSON
│   ├── instagram_scraper.py  # FR-04: Instaloader profile fetch
│   ├── web_researcher.py     # FR-05: DuckDuckGo + Gemini synthesis
│   └── sheets_writer.py      # FR-06: Google Sheets append/update
│
├── slack_handlers/
│   ├── __init__.py
│   └── events.py             # Slack Bolt event listeners (file_shared)
│
├── utils/
│   ├── __init__.py
│   ├── logger.py             # Structured JSON logger
│   └── retry.py              # Exponential backoff decorator
│
└── tests/
    ├── __init__.py
    ├── unit/
    │   ├── test_vision_extractor.py
    │   ├── test_instagram_scraper.py
    │   ├── test_web_researcher.py
    │   ├── test_sheets_writer.py
    │   ├── test_downloader.py
    │   └── test_config.py
    ├── integration/
    │   ├── test_pipeline_flow.py
    │   └── test_slack_events.py
    └── fixtures/
        ├── sample_brand_post.jpg
        ├── mock_gemini_response.json
        └── mock_instagram_profile.json
```

### 6.1 Module Responsibilities

| Module / File | Responsibility | Key Libraries |
|---|---|---|
| `main.py` | Creates FastAPI app (`api`), mounts Bolt adapter on `POST /slack/events`, exposes `GET /health`, starts Uvicorn | fastapi, slack_bolt, uvicorn |
| `config.py` | Load .env / Render env vars, fail fast if missing | python-dotenv |
| `pipeline/downloader.py` | Download Slack image to /tmp, validate, return path | requests, Pillow |
| `pipeline/vision_extractor.py` | Call Gemini Flash vision, parse JSON response | google-generativeai |
| `pipeline/instagram_scraper.py` | Instaloader fetch, parse profile fields | instaloader |
| `pipeline/web_researcher.py` | DDG search + Gemini Flash synthesis | duckduckgo-search |
| `pipeline/sheets_writer.py` | gspread append/update, dedup by handle | gspread |
| `slack_handlers/events.py` | Slack Bolt event listeners — `file_shared` handler, orchestrates pipeline stages | slack_bolt |
| `utils/logger.py` | Structured JSON logs with stage/level/message fields | logging |
| `utils/retry.py` | Decorator: retry N times with exponential backoff | functools, time |

---

## 7. Environment Variables

All secrets are stored as environment variables. On Render, set these in **Dashboard → Your Service → Environment**. For local development, copy `.env.example` to `.env`.

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | `xoxb-...` token from Slack App settings |
| `SLACK_SIGNING_SECRET` | Yes | Signing secret from Slack App settings |
| `SLACK_CHANNEL_ID` | Yes | Channel ID where boss shares posts |
| `GEMINI_API_KEY` | Yes | Free API key from aistudio.google.com |
| `GOOGLE_SHEET_ID` | Yes | ID from the Google Sheet URL |
| `GOOGLE_SHEET_TAB` | No | Tab name — defaults to `"Brand Research"` |
| `GOOGLE_CREDS_JSON` | Yes | Service account JSON (base64 encoded) |
| `INSTAGRAM_USERNAME` | No | Optional — improves Instaloader reliability |
| `INSTAGRAM_PASSWORD` | No | Optional — paired with above |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` — defaults to `INFO` |
| `MAX_RETRIES` | No | Gemini retry attempts — defaults to `3` |
| `PORT` | No | Server port — Render sets this automatically |

> ⚠️ **Security:** Never commit `.env` to Git. Add `.env` to `.gitignore` immediately. The `GOOGLE_CREDS_JSON` must be base64-encoded before pasting into Render.

---

## 8. Data Model — Google Sheet Columns

One row per brand. **Column order is fixed** — do not rearrange or the writer module will break.

| # | Column Name | Source | Notes |
|---|---|---|---|
| 1 | Timestamp | System | ISO 8601 — auto-set at write time |
| 2 | Brand Name | Gemini Vision | Extracted from image |
| 3 | Instagram Handle | Gemini Vision | @handle — without the @ symbol |
| 4 | Niche / Category | Gemini Vision | e.g. Skincare, Fashion, F&B |
| 5 | Followers | Instaloader | Integer — formatted with commas |
| 6 | Following | Instaloader | Integer |
| 7 | Total Posts | Instaloader | Integer |
| 8 | Website URL | Instaloader | From bio link — may be empty |
| 9 | Email | Gemini + DDG | Extracted if visible in post/web |
| 10 | Phone | Gemini + DDG | Extracted if visible in post/web |
| 11 | Bio | Instaloader | Full bio text |
| 12 | Is Verified | Instaloader | TRUE / FALSE |
| 13 | Research Notes | Gemini Flash | 150-200 word synthesised brief |
| 14 | Source Post URL | Slack | Slack permalink of the shared image |
| 15 | Status | System | Default: `To Contact` |

---

## 9. Internal API Contracts

Each pipeline module must conform to these function signatures. This enables independent unit testing and easy replacement of any module.

### 9.1 `downloader.download_image(file_info, slack_client)`

```python
# Input:  file_info (dict)  — Slack file object from event payload
#         slack_client      — authenticated Slack WebClient instance
# Output: str               — absolute path to downloaded temp file
# Raises: DownloadError     — if download fails after retries
#         ValidationError   — if file is not a valid image
```

### 9.2 `vision_extractor.extract_brand(image_path)`

```python
# Input:  image_path (str)  — path to downloaded image file
# Output: dict {
#           brand_name:   str,
#           handle:       str,   # without @
#           niche:        str,
#           tagline:      str,
#           email:        str | None,
#           phone:        str | None,
#           website:      str | None,
#           confidence:   float  # 0.0–1.0
#         }
# Raises: ExtractionError  — if Gemini fails after retries
```

### 9.3 `instagram_scraper.get_profile(handle)`

```python
# Input:  handle (str)      — Instagram handle without @
# Output: dict {
#           full_name:    str,
#           bio:          str,
#           followers:    int,
#           following:    int,
#           post_count:   int,
#           website:      str | None,
#           is_verified:  bool,
#           is_private:   bool
#         }
# Raises: ProfileNotFoundError — handle does not exist
#         PrivateProfileError  — profile exists but is private
```

### 9.4 `web_researcher.research_brand(brand_name, handle)`

```python
# Input:  brand_name (str), handle (str)
# Output: dict {
#           research_notes: str,       # 150-200 word brief
#           sources:        list[str]  # URLs used
#         }
# Raises: ResearchError    — if all searches fail
```

### 9.5 `sheets_writer.write_brand(brand_data)`

```python
# Input:  brand_data (dict) — merged output of all pipeline stages
# Output: dict {
#           action:  "appended" | "updated",
#           row_num: int
#         }
# Raises: SheetWriteError  — if Sheets API call fails
```

---

## 10. Testing Strategy

The project uses a three-layer testing approach. All tests run with **pytest**. Target: **80%+ code coverage** across all pipeline modules.

### 10.1 Unit Tests

Each pipeline module is tested in complete isolation. External services (Gemini, Instaloader, Google Sheets, DuckDuckGo) are fully mocked using `unittest.mock.patch`. Unit tests must run with **no internet connection**.

| Test File | Method | What is Tested |
|---|---|---|
| `test_config.py` | pytest | Missing required env vars raise `ValueError` on startup |
| `test_downloader.py` | pytest + mock | Valid image saves to /tmp; non-image raises `ValidationError`; Slack 403 raises `DownloadError` |
| `test_vision_extractor.py` | pytest + mock | Valid Gemini JSON parsed correctly; malformed JSON raises `ExtractionError`; retry logic fires on 429 |
| `test_instagram_scraper.py` | pytest + mock | Public profile returns correct dict; private profile raises `PrivateProfileError`; unknown handle raises `ProfileNotFoundError` |
| `test_web_researcher.py` | pytest + mock | DDG returns results → Gemini synthesises brief; empty DDG results → fallback brief generated |
| `test_sheets_writer.py` | pytest + mock | New handle appends row; existing handle updates row; API failure raises `SheetWriteError` |

```bash
# Run unit tests
pytest tests/unit/ -v --cov=pipeline --cov-report=term-missing
```

### 10.2 Integration Tests

Tests that run the full pipeline using real module interactions but with external APIs still mocked at the HTTP level (using `responses` or `pytest-httpx`). These verify that modules wire together correctly.

| Test File | Method | What is Tested |
|---|---|---|
| `test_pipeline_flow.py` | pytest + responses | Full pipeline: mock image → mock Gemini → mock Instaloader → mock DDG → assert Sheet row written correctly |
| `test_pipeline_flow.py` | pytest + responses | Pipeline with private Instagram profile: row written with `is_private=True`, scrape fields empty, no crash |
| `test_pipeline_flow.py` | pytest + responses | Pipeline with Gemini low confidence: row written with `status="Review Needed"` |
| `test_slack_events.py` | pytest + mock | `file_shared` event with image triggers pipeline; PDF is ignored; bot message is ignored |

```bash
# Run integration tests
pytest tests/integration/ -v
```

### 10.3 End-to-End (Smoke) Test

One manual smoke test to run after each deployment. No automation — just a checklist.

- [ ] Share a real Instagram brand post screenshot in the configured Slack channel
- [ ] Confirm bot replies in thread within 5 seconds: `"Processing your post..."`
- [ ] Confirm bot replies again within 90 seconds: `"✅ Done — check the Sheet"`
- [ ] Open Google Sheet — confirm new row has appeared with correct brand name
- [ ] Check all 15 columns are populated (some may be empty for private profiles — acceptable)
- [ ] Hit `GET /health` — confirm 200 response with `{"status": "ok"}`

### 10.4 Edge Case Tests (Unit)

These must be covered inside the unit test files — listed explicitly so they are not missed:

- Image is a screenshot with no visible handle — Gemini returns `handle: null`
- Handle extracted has `@` prefix — scraper strips it before use
- Brand already exists in Sheet (dedup test) — row is updated, not duplicated
- Gemini API returns HTTP 429 (rate limit) — exponential backoff retries 3 times
- Instaloader throws `ConnectionError` — pipeline continues with empty profile data
- Google Sheets token expired — refresh attempted, failure raises `SheetWriteError`
- Image file deleted before Gemini call — raises `FileNotFoundError`, Slack error posted

### 10.5 Test Coverage Requirements

| Module | Minimum Coverage |
|---|---|
| `pipeline/downloader.py` | ≥ 85% |
| `pipeline/vision_extractor.py` | ≥ 85% |
| `pipeline/instagram_scraper.py` | ≥ 80% |
| `pipeline/web_researcher.py` | ≥ 80% |
| `pipeline/sheets_writer.py` | ≥ 85% |
| `slack_handlers/events.py` | ≥ 75% |
| `utils/retry.py` | ≥ 90% |
| **Overall** | **≥ 80%** |

---

## 11. Dependencies (requirements.txt)

```
# Web framework — FastAPI as outer server, Uvicorn as ASGI runner
fastapi>=0.111.0
uvicorn[standard]>=0.29.0

# Slack — Bolt for event handling, mounted on FastAPI via official adapter
slack-bolt>=1.18.0
slack-sdk>=3.27.0

# Google AI (Gemini Flash — free tier)
google-generativeai>=0.7.0

# Instagram scraping (no API key needed)
instaloader>=4.10.3

# Web research (no API key needed)
duckduckgo-search>=6.1.0

# Google Sheets
gspread>=6.1.2
google-auth>=2.29.0

# Image validation
Pillow>=10.3.0

# HTTP downloads
requests>=2.31.0
httpx>=0.27.0

# Env vars
python-dotenv>=1.0.1

# Testing
pytest>=8.2.0
pytest-cov>=5.0.0
pytest-asyncio>=0.23.7
responses>=0.25.0
httpx>=0.27.0
```

---

## 12. Deployment Guide

### Step 1 — Slack App Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From Scratch
2. Under **OAuth & Permissions → Bot Token Scopes**, add: `channels:history`, `files:read`, `chat:write`, `channels:join`
3. Under **Event Subscriptions → Enable Events** → Subscribe to bot events: `message.channels`, `file_shared`
4. Install app to workspace → copy **Bot User OAuth Token** (`SLACK_BOT_TOKEN`)
5. Copy **Signing Secret** from Basic Information (`SLACK_SIGNING_SECRET`)

### Step 2 — Gemini API Key

1. Go to [aistudio.google.com](https://aistudio.google.com) → Sign in with Google account
2. Click **"Get API Key"** → Create API Key in new project
3. Copy the key → set as `GEMINI_API_KEY`

> No credit card required. Gemini 2.0 Flash free tier: 15 RPM, 1500 RPD.

### Step 3 — Google Sheets & Service Account

1. Create a new Google Sheet → copy the ID from the URL
2. Go to [console.cloud.google.com](https://console.cloud.google.com) → Create Project → Enable **Google Sheets API**
3. Create **Service Account** → download JSON credentials
4. Base64-encode the JSON: `cat creds.json | base64`
5. Share the Google Sheet with the service account email (Editor access)
6. Set `GOOGLE_CREDS_JSON` (base64 value) and `GOOGLE_SHEET_ID` as env vars

### Step 4 — Deploy to Render

1. Push code to a GitHub repository
2. Go to [render.com](https://render.com) → New → Web Service → Connect GitHub repo
3. Runtime: **Python 3** | Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn main:api --host 0.0.0.0 --port $PORT`
5. Add all environment variables in the **Environment** tab
6. Deploy — copy the Render URL (e.g. `https://brand-bot.onrender.com`)

> `main:api` refers to the FastAPI instance named `api` inside `main.py` — Uvicorn runs it directly.

### Step 5 — Connect Slack Webhook to Render

1. In Slack App settings → **Event Subscriptions → Request URL**
2. Enter: `https://your-app.onrender.com/slack/events`
3. Slack will send a challenge request — your app responds automatically
4. Once verified, Slack events will flow to Render

### Step 6 — Set Up cron-job.org Keepalive

1. Create free account at [cron-job.org](https://cron-job.org)
2. New Cronjob → URL: `https://your-app.onrender.com/health`
3. Schedule: **Every 5 minutes**
4. Enable → Save

Your Render app will now never sleep. ✅

---

## 13. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Instaloader blocked by Instagram | Medium | Add random delays; use optional IG login; handle gracefully with empty scrape data |
| Gemini free tier rate limit (429) | Low | Exponential backoff with 3 retries; pipeline processes one post at a time |
| Render spins down despite keepalive | Low | cron-job.org pings every 5 min; `/health` responds in <500ms |
| Boss sends non-Instagram image | Medium | Gemini returns low confidence; row flagged `"Review Needed"`; no crash |
| Google Sheet token expires | Low | gspread auto-refreshes; `SheetWriteError` posted to Slack if refresh fails |
| DuckDuckGo rate limits search | Low | Add 1s delay between queries; fallback to shorter brief if all searches fail |

---

## 14. Future Scope (v2+)

- Auto-draft outreach email using Gemini and Gmail API
- Support for WhatsApp trigger (via Whapi.Cloud free tier)
- Support for TikTok and YouTube brand detection
- Slack slash command: `/research @handle` — manual trigger without image
- Weekly digest: auto-send summary of all new brands to Slack channel
- Status update from Sheet back to Slack (e.g. mark "Contacted" via reaction)

---

*— End of Document —*
