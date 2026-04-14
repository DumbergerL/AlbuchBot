# AlbuchBot

AlbuchBot contains:

- `www`: a short static webpage that explains the project and links to the WhatsApp channel.
- `agent`: a Python implementation that pulls the latest PDF from the Albuch-Bote page, extracts text, and asks Gemini to return 7 categorized news entries.
- The agent stores JSON output and pushes the 7 extracted news entries into a Google Spreadsheet.

## Project structure

```text
www/
  index.html
agent/
  scraper.py            # Step 1: PDF extraction + LLM -> JSON
  processor.py          # Step 2: JSON -> Google Sheets
  build_email_digest.py # Step 3 helper: JSON -> email subject/body text
  requirements.txt
.github/workflows/
  daily-agent.yml       # Split pipeline with parallel jobs (Sheets + Email)
```

## Web page

Open `www/index.html` in a browser and replace the placeholder WhatsApp URL with your real channel URL.

## Python agent

### 1) Install dependencies

```bash
cd agent
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Lokale Konfiguration per `.env` ist unterstuetzt.

1. Kopiere `agent/.env.example` nach `agent/.env`
2. Trage dort deine Werte ein (`GEMINI_API_KEY`, optional `GEMINI_MODEL`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SPREADSHEET_ID`, optional `GOOGLE_WORKSHEET_NAME`)
3. Starte den Bot wie gewohnt mit `python main.py` oder `py agent/main.py`

Beide Scripts (scraper.py und processor.py) laden automatisch Umgebungsvariablen aus einer `.env` im Projekt-Root und aus `agent/.env`.

TLS/SSL Hinweis:

- Der Bot nutzt den Betriebssystem-Zertifikatsspeicher ueber `truststore` (hilft bei Corporate-Proxies und CERTIFICATE_VERIFY_FAILED).
- Optional kannst du ein eigenes CA-Bundle ueber `SSL_CERT_FILE` setzen.

Hinweis zum Gemini SDK:

- Das alte Paket `google.generativeai` ist abgeloest.
- Dieses Projekt verwendet jetzt `google-genai` (`from google import genai`).
- Falls vorher installiert, entferne es optional mit `pip uninstall google-generativeai`.

### 2) Configure Gemini API key

```powershell
$env:GEMINI_API_KEY = "YOUR_API_KEY"
```

### 3) Configure Google Sheets access

The bot writes rows into a spreadsheet using a Google service account.

1. In Google Cloud Console, create a project (or use an existing one).
2. Enable the `Google Sheets API` for that project.
3. Create a service account and generate a JSON key file.
4. Create a spreadsheet in Google Sheets and copy its Spreadsheet ID from the URL.
5. Share the spreadsheet with the service-account email (for example `albuchbot-writer@your-project.iam.gserviceaccount.com`) and grant `Editor` rights.
6. Set environment variables:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON = "C:\path\to\service-account.json"
$env:GOOGLE_SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
$env:GOOGLE_WORKSHEET_NAME = "AlbuchBot News"
```

### 4) Local: Run split pipeline steps

**Step 1: Scraper (PDF extraction + LLM)**

```bash
python agent/scraper.py --output output/news.json
```

Optional flags:

```bash
python agent/scraper.py --output output/news.json --model gemini-2.5-flash-lite --log-level DEBUG
```

Force a manual re-analysis even if the selected PDF title matches the last processed document:

```bash
python agent/scraper.py \
  --output output/news.json \
  --last-processed-document-title "Albuch Bote KW 14/2026 (02.04.2026) (PDF-Dokument)" \
  --force-process
```

Alternative via env:

```bash
FORCE_PROCESS=true
python agent/scraper.py
```

**Step 2: Processor (JSON → Google Sheets)**

Run this after Step 1 produces `output/news.json`:

```bash
python agent/processor.py --input output/news.json
```

`processor.py` reads `GOOGLE_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_WORKSHEET_NAME` automatically from env or `agent/.env`.

Optional override via CLI:

```bash
python agent/processor.py \
  --input output/news.json \
  --spreadsheet-id "YOUR_SPREADSHEET_ID" \
  --service-account-json "C:\path\to\service-account.json"
```

Both steps can be run independently or in sequence.

**Optional local Step 3: Build email digest text**

```bash
python agent/build_email_digest.py \
  --input output/news.json \
  --run-state output/run_state.json \
  --subject-output output/email_subject.txt \
  --body-output output/email_body.txt
```

This creates:
- `output/email_subject.txt`
- `output/email_body.txt`

The GitHub pipeline uses these files for email sending.

**Optional local Step 4: Send email digest**

```bash
# Prerequisite: set email env vars or create agent/.env with MAIL_* values
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USER=your-email@gmail.com
MAIL_PASS=your-app-password
MAIL_TO=recipient@example.com
MAIL_FROM=your-email@gmail.com

python agent/send_email_digest.py \
  --subject output/email_subject.txt \
  --body output/email_body.txt \
  --attachments output/news.json
```

Features:
- Improved logging (startup, connection, auth, sending, errors)
- DNS validation before SMTP connection (detects mistyped hostnames early)
- SMTP configuration validation
- Multiple attachments supported
- UTF-8 support
- Automatic error reporting with actionable hints

**Troubleshooting email sending:**

If you see `No address associated with hostname`:
1. Check your `MAIL_SERVER` secret in GitHub (Settings → Secrets and variables → Actions)
2. Common issues:
   - Typo: `smtp.gmails.com` should be `smtp.gmail.com`
   - Empty secret value
   - Protocol included: `https://smtp.gmail.com` (remove the `https://`)

If you see `Authentication failed`:
1. Verify `MAIL_USER` and `MAIL_PASS` are correct
2. For Gmail: use an **App Password**, not your regular password
3. For Office 365: verify Basic Auth is enabled in your tenant

## Output format

**Step 1 (Scraper) produces JSON** with:
- source listing URL
- selected PDF URL
- pdf_link_name (human-readable name for HTML display, e.g. "AlbuchBote 16.01.2026")
- exactly 16 news entries (4 gemeinderat + 4 vereine + 4 kirchliche + 4 general)
- Each entry has: title, summary, source_excerpt

**Step 2 (Processor) writes to Google Sheets** with columns:
- generated_at_utc
- category
- title
- summary
- source_excerpt
- pdf_link_name (clickable link display name)
- listing_url
- pdf_url

The processor appends new rows to the worksheet and does not overwrite existing entries.

## GitHub daily pipeline

Workflow file: `.github/workflows/daily-agent.yml`

- Runs every day at 06:00 UTC
- Can also be started manually via `workflow_dispatch`
- Manual runs expose a `force_process` checkbox to reprocess the latest document even if it was already handled before
- **Job 1** (scraper): Fetches PDF, extracts text, runs Gemini LLM -> outputs `output/news.json` as artifact
- **Job 2** (processor): Downloads artifact from Job 1, reads JSON, uploads to Google Sheets
- **Job 3** (email digest): Runs in parallel to Job 2, builds a readable flow-text digest from `news.json`, then sends it by email
- If Job 2 or Job 3 fails, Job 1 output (`news.json`) is preserved as artifact for manual recovery

Required GitHub repository secrets (`.github/workflows/daily-agent.yml`):

1. Go to `Settings > Secrets and variables > Actions`
2. Create repository secret `GEMINI_API_KEY` (required for Step 1 scraper)
3. Create repository secret `GOOGLE_SERVICE_ACCOUNT_JSON` with the full JSON key content (required for Step 2 processor)
4. Create repository secret `GOOGLE_SPREADSHEET_ID` (required for Step 2 processor)
5. For optional email digest sending (Step 3):
   - `MAIL_SERVER` (for example `smtp.office365.com` or `smtp.gmail.com`)
   - `MAIL_PORT` (for example `587`)
   - `MAIL_USER`
   - `MAIL_PASS`
   - `MAIL_TO` (single address or comma-separated list)
   - `MAIL_FROM` (optional, falls back to `MAIL_USER`)

Spreadsheet permissions for pipeline Step 2:

1. Open your target Google Spreadsheet
2. Click Share
3. Add the service account email from `GOOGLE_SERVICE_ACCOUNT_JSON`
4. Grant `Editor` access

## Pipeline resilience

With the split architecture:
- If Step 1 (scraper) fails early, pipeline stops and no artifact is pushed
- If Step 1 succeeds, the news.json artifact is available even if Step 2 (processor) fails
- You can manually run Step 2 later if needed (download the artifact, adjust config, rerun processor.py)
- Step 1 can be triggered independently from GitHub Actions UI for testing