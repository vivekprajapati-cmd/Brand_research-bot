# Services & APIs Used

## Slack
- **Purpose:** Event trigger — receives Instagram URLs and screenshot uploads
- **Auth:** Bot token (`SLACK_BOT_TOKEN`) + signing secret (`SLACK_SIGNING_SECRET`)
- **Events subscribed:** `message.im`, `message.channels`, `file_shared`
- **Free tier:** Yes (no limits for basic bot usage)

## Google Gemini API
- **Purpose:** Vision extraction (read brand info from post images) + web research synthesis
- **Model:** `gemini-3.1-flash-lite`
- **Auth:** API key (`GEMINI_API_KEY`) via Google AI Studio
- **Free tier:** 1,500 requests/day, 30 RPM
- **Console:** [aistudio.google.com](https://aistudio.google.com)

## Google Sheets API
- **Purpose:** Stores extracted brand data (one row per brand)
- **Auth:** Service account JSON (`GOOGLE_CREDS_JSON`, base64-encoded)
- **Sheet ID:** `GOOGLE_SHEET_ID` env var
- **Tab name:** `Brand Research` (auto-created if missing)
- **Free tier:** Yes (Google Workspace / personal account)

## Google Cloud (Service Account)
- **Purpose:** Authenticates the Sheets API calls
- **Project:** `marine-clarity-505207-b9`
- **Service account:** `brand-research-bot@marine-clarity-505207-b9.iam.gserviceaccount.com`
- **Console:** [console.cloud.google.com](https://console.cloud.google.com)

## Instaloader
- **Purpose:** Downloads post image from Instagram URL, extracts `owner_username`
- **Auth:** None (unauthenticated — public posts only)
- **Limitation:** Instagram rate-limits at ~429 on profile scrape; image download is unaffected
- **Free:** Yes (open source Python library)

## DuckDuckGo Search (ddgs)
- **Purpose:** Web research — 3 searches per brand to collect snippets for Gemini synthesis
- **Auth:** None (no API key needed)
- **Free:** Yes (open source Python library)

## Render
- **Purpose:** Hosts the FastAPI server (production deployment)
- **Start command:** `uvicorn main:api --host 0.0.0.0 --port $PORT`
- **URL:** `https://brand-research-bot.onrender.com`
- **Health check:** `GET /health`
- **Slack events endpoint:** `POST /slack/events`
- **Free tier:** Yes (spins down after inactivity on free plan)

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | Bot OAuth token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Yes | Slack app signing secret |
| `SLACK_CHANNEL_ID` | Yes | Channel/DM ID the bot listens to |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `GOOGLE_SHEET_ID` | Yes | Google Sheets document ID |
| `GOOGLE_CREDS_JSON` | Yes | Base64-encoded service account JSON |
| `GOOGLE_SHEET_TAB` | No | Worksheet name (default: `Brand Research`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `PORT` | No | Server port (default: `8080`) |
