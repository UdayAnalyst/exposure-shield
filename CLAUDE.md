# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

ExposureShield is a web app for the "Hack for Humanity" hackathon (UConn, Sept 30, 2026). A user enters their email address and instantly sees their entire digital footprint: every platform they're registered on, every data breach they've been in, a risk score, and a step-by-step cleanup plan.

This is a one-day hackathon build. Speed matters. Ship working code, not perfect code.

---

## Project Structure

```
exposure-shield/
├── CLAUDE.md                  # You are here
├── backend/
│   ├── app.py                 # FastAPI server (main entry point)
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── accounts.py        # Combines both account scanners (entry point for Step 2)
│   │   ├── userscanner_scan.py # user-scanner integration — primary account discovery
│   │   ├── holehe_scan.py     # Holehe integration — sites user-scanner lacks
│   │   ├── hibp_scan.py       # HaveIBeenPwned API — find breaches
│   │   └── justdelete.py      # JustDeleteMe data — deletion URLs
│   ├── scoring/
│   │   ├── __init__.py
│   │   └── engine.py          # Exposure score calculator (0-100)
│   └── requirements.txt
├── frontend/
│   └── index.html             # Single-file frontend (HTML + CSS + JS, no framework)
└── data/
    └── justdelete.json         # Cached JustDeleteMe platform deletion data
```

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, uvicorn
- **Frontend:** Single HTML file, vanilla JS, no React/Vue/npm (keep it simple, hackathon speed)
- **Key Libraries:**
  - `user-scanner` + `holehe` (pip) — check where an email is registered (~230 sites combined; see Step 2)
  - `httpx` — async HTTP client for HaveIBeenPwned API calls
  - `fastapi` + `uvicorn` — API server
- **External APIs:**
  - HaveIBeenPwned v3 API (https://haveibeenpwned.com/API/v3)
    - Endpoint: `GET https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false`
    - Header: `hibp-api-key: {API_KEY}` — **required** for per-email lookups (paid subscription). There is no free unauthenticated per-email endpoint.
    - Header: `user-agent: ExposureShield-Hackathon`
    - If no API key is set, use demo mode (realistic mock data). The free `GET /api/v3/breaches` endpoint (full breach catalog, no key needed) can be used to make mock data realistic.
- **No database.** Everything is computed per-request. No user data is stored.

---

## Build Order (follow this sequence)

### Step 1: Backend scaffold
- Create the FastAPI app with CORS enabled (frontend will call from browser)
- Single POST endpoint: `/api/scan` accepting `{"email": "user@example.com"}`
- Return JSON response structure (define the schema first, fill in logic after)

### Step 2: Account discovery (DONE — `scanner/accounts.py`)
Two sources run concurrently, merged and de-duplicated by domain:
- **user-scanner** (`scanner/userscanner_scan.py`, kaifcodec/user-scanner, MIT) — primary. Actively maintained holehe successor; ~151 email modules, ~125 give a definite answer. We call each module's `validate_*` function directly — do NOT use its `email_orchestrator` (draws a rich progress bar and monkey-patches httpx proxies/timeouts globally).
- **holehe** (`scanner/holehe_scan.py`) — only for the ~80 sites user-scanner doesn't cover (`HOLEHE_SKIP` in `accounts.py`); ~30 of those answer. holehe 1.61 (2022) is unmaintained. Its modules are `async fn(email, client, out)`; its CLI uses trio but they run fine under asyncio.

Numbers (2026-09-26): ~156 of 234 sites answer, scan takes ~9s via the API, **zero false positives** on a random unregistered address from either source. Re-run that fake-address check after any scanner change.

Test without the server (from `backend/`): `python -m scanner.accounts someone@example.com` (also `scanner.holehe_scan`).

Non-obvious fixes — don't undo them:
- **Never run alerting modules.** user-scanner's `is_loud()` list and holehe's `adobe`/`mail_ru`/`odnoklassniki`/`samsung` can notify the account owner (reset emails, codes). The pitch promises "no alerts are sent".
- **Adult sites are hidden by default** (user-scanner `no_nsfw`, holehe `porn` category) because results go on a projector.
- **holehe needs `follow_redirects=True`.** It was written for httpx < 0.20, which followed redirects by default; without it ~10 sites misreport as rate-limited.
- **Shared SSL context** (top of `userscanner_scan.py`): each module creates its own `httpx.AsyncClient`, and each one loads the CA bundle synchronously (~35ms). ~170 of those froze the event loop for 6–10s per scan. We patch `httpx.AsyncClient.__init__` to default `verify` to one shared context.
- Per-site timeout is 6s (measured: all answers arrive within 6s). The remaining failures are sites that block automated requests (Cloudflare/DataDome/captcha), dead endpoints, or changed pages. We deliberately don't try to get around bot protection.
- `user-scanner` is pinned (`==1.5.2`) because its module API changes often. Upgrading usually adds sites, but re-test first.

### Step 3: HaveIBeenPwned integration (`hibp_scan.py`)
- Call HIBP API for the email
- Parse response: breach name, date, data classes (Passwords, Email, Phone, etc.)
- Handle 404 (no breaches found) gracefully — that's a good result
- Handle rate limiting (429 → honor `retry-after` header, retry with backoff)
- If API key is not available, provide a mock/demo mode with realistic sample data

### Step 4: JustDeleteMe mapping (`justdelete.py`)
- Source: https://github.com/jdm-contrib/jdm (the `sites.json` data file — verify its current path in the repo)
- Download and cache as `data/justdelete.json`
- For each platform from holehe results, look up:
  - Direct deletion URL
  - Difficulty rating (easy/medium/hard/impossible)
- Match by domain name (fuzzy match acceptable)

### Step 5: Scoring engine (`scoring/engine.py`)
- Input: holehe results + HIBP results
- Scoring formula:
  ```
  base = 0
  base += len(registered_platforms) * 1.5        # More accounts = more risk
  base += len(breaches) * 8                       # Each breach is serious
  base += count_of("Passwords" in breach_data) * 15  # Password leaks are critical
  base += count_of("Phone numbers" in breach_data) * 5
  base += count_of("Physical addresses" in breach_data) * 10
  base += count_of("IP addresses" in breach_data) * 3

  # Platforms that were BOTH registered AND breached get extra weight
  overlap = platforms_that_are_also_breached
  base += len(overlap) * 12

  score = min(int(base), 100)  # Cap at 100
  ```
- Return score + severity label:
  - 0-25: "Low" (green)
  - 26-50: "Moderate" (yellow)
  - 51-75: "High" (orange)
  - 76-100: "Critical" (red)

### Step 6: Merge and respond
- The `/api/scan` endpoint runs holehe + HIBP concurrently (asyncio.gather)
- Cross-references: for each registered platform, check if it also appears in breach list (match HIBP `Domain` against holehe `domain`)
- Generates cleanup plan: prioritized list of actions
- Returns full JSON:
  ```json
  {
    "email": "user@example.com",
    "scan_time_seconds": 12.4,
    "platforms": [
      {
        "name": "Instagram",
        "domain": "instagram.com",
        "exists": true,
        "breached": true,
        "breach_date": "2019-03-01",
        "data_leaked": ["Passwords", "Email addresses", "Phone numbers"],
        "risk_level": "critical",
        "delete_url": "https://instagram.com/accounts/remove/request/permanent/",
        "delete_difficulty": "easy"
      }
    ],
    "breaches": [
      {
        "name": "LinkedIn",
        "date": "2021-06-22",
        "data_classes": ["Email addresses", "Phone numbers"],
        "description": "..."
      }
    ],
    "score": {
      "value": 73,
      "label": "High",
      "color": "orange"
    },
    "cleanup_plan": [
      {
        "priority": 1,
        "action": "Change password on Instagram (breached, password leaked)",
        "url": "https://instagram.com/accounts/password/change/",
        "type": "change_password"
      },
      {
        "priority": 2,
        "action": "Delete unused account on Spotify",
        "url": "https://www.spotify.com/account/close/",
        "difficulty": "easy",
        "type": "delete_account"
      }
    ],
    "summary": {
      "total_platforms": 34,
      "total_breaches": 5,
      "passwords_leaked": 2,
      "accounts_to_delete": 18,
      "accounts_to_secure": 4
    }
  }
  ```

### Step 7: Frontend (`index.html`)
- Single HTML file with embedded CSS and JS
- Design: dark theme, clean, slightly ominous feel (this is about exposure)
- Sections:
  1. **Hero/Input:** centered email input + "Scan Me" button
  2. **Loading state:** animated progress showing scan stages ("Checking platforms...", "Scanning breaches...", "Calculating risk...")
  3. **Results — Score:** big circular gauge showing 0-100 score with color
  4. **Results — Platform Grid:** cards for each platform, color-coded by risk
  5. **Results — Breach Timeline:** chronological list of breaches
  6. **Results — Cleanup Plan:** prioritized action checklist with direct links
  7. **Footer:** "Built for Hack for Humanity 2026 | Your data never leaves this session"
- Use `fetch()` to call `/api/scan`
- Add a "Scan Another Email" button to reset
- Make it mobile responsive (judges may look at it on phones)

---

## Critical Rules

1. **Never store any user data.** No database, no logs of emails scanned, no cookies. This is a privacy tool — practice what you preach. (This includes uvicorn access logs / debug prints that echo the email.)
2. **Account discovery takes ~9s.** Show a realistic loading animation; the scan is bounded by the 6s per-site timeout. Show results as they stream in if possible, or show a realistic loading animation. Run holehe with a timeout per site (2-3 seconds) to cap total scan time.
3. **Handle errors gracefully.** If HIBP rate-limits you, show holehe results anyway. If holehe times out on some sites, show partial results. Never show a blank screen.
4. **The demo is everything.** Test with your own emails first. Have 2-3 backup emails ready if the live demo fails.
5. **Attribution:** Include a credits section: "Powered by user-scanner (kaifcodec), Holehe (megadose), HaveIBeenPwned (Troy Hunt), JustDeleteMe (jdm-contrib)" — open source credit matters.

---

## Running the Project

```bash
# Install dependencies
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start the server
uvicorn app:app --host 0.0.0.0 --port 8000 --reload --loop asyncio

# Frontend: just open frontend/index.html in a browser
# Or serve it: python -m http.server 3000 --directory frontend

# Quick API check
curl -X POST localhost:8000/api/scan -H 'Content-Type: application/json' -d '{"email":"test@example.com"}'
```

---

## Deployment (GitHub → Azure VM)

**Pushing to `main` deploys automatically** (`.github/workflows/deploy.yml`; markdown-only changes are skipped). The workflow runs `deploy/deploy.sh`, then checks `/api/health`.
- **The VM is kept deallocated to save credits and started only for the demo** (Cloud Shell: `az vm start -g exposureshield-rg -n exposureshield-vm`, then `az vm deallocate ...` after). While it's off, the site is down and pushes show a failed deploy — expected, not a bug. The Mac has no Azure CLI.
- URL: https://exposureshield-uconn.mexicocentral.cloudapp.azure.com. Region is **Mexico Central** because the student subscription's policy blocks East US.
- VM: Ubuntu 24.04, B1s, Python 3.12, resource group `exposureshield-rg`. Delete it all after the hackathon with `az group delete -n exposureshield-rg`.
- GitHub config:
  - repo variable `VM_HOST`
  - secret `VM_SSH_KEY`: the private half of `~/.ssh/exposureshield_gh_deploy`, a key used only by GitHub
  - secret `VM_KNOWN_HOSTS`: pinned VM host keys
- Manual deploy from the Mac: `./deploy/deploy.sh azureuser@<host> <host>`. It's safe to re-run. Your personal SSH key is `~/.ssh/exposureshield_azure`.
- On the VM:
  - Code is in `/opt/exposureshield`.
  - The `exposureshield` systemd service runs uvicorn on `127.0.0.1:8000` with `--no-access-log`.
  - Caddy serves `frontend/`, proxies `/api/*` to the app, and handles HTTPS on its own.
  - Secrets go in `/etc/exposureshield.env` (e.g. `HIBP_API_KEY`), then `sudo systemctl restart exposureshield`.
  - Logs: `sudo journalctl -u exposureshield -f`.
- **Always run uvicorn with `--loop asyncio`, never uvloop.** Measured on the VM: uvloop cut answering sites from ~143 to ~50–80 of 234 and slowed scans from ~9s to ~15s.
- Datacenter IP: with asyncio, the VM gets ~143 answers and your Mac ~156. A few sites (Facebook, some behind a WAF) block Azure's IP.

---

## Environment Variables

```
HIBP_API_KEY=your_key_here        # Optional — falls back to demo mode if not set
HOST=0.0.0.0
PORT=8000
```

---

## Demo Script (for the hackathon pitch)

1. Open the website on the projector
2. Ask a judge: "Can I borrow your email for 60 seconds?"
3. Type it in, hit scan
4. While it loads, explain: "Right now we're silently checking over 200 platforms using their own forgot-password endpoints. No alerts are sent."
5. Results appear. Point out: "You have accounts on 37 platforms. 4 of them were breached. Your LinkedIn password was leaked in 2021."
6. Show the score. "Your exposure score is 73 out of 100."
7. Show cleanup plan. "Here's your personalized fix — delete these 12 accounts you forgot about, change these 3 passwords, and your score drops to 20."
8. Close with: "Every person in this room has a score like this. Most have never seen it."

---

## Working Notes for Claude Code

- This is a HACKATHON project. Do not over-engineer. No Docker, no CI/CD, no tests.
- The frontend is ONE HTML file. No npm, no build step, no framework.
- If something is hard to implement in 30 minutes, skip it and add a "coming soon" label.
- Always show partial results rather than waiting for everything to finish.
- The scoring formula doesn't need to be scientifically rigorous — it needs to feel right and look dramatic on stage.
- Site checks are flaky. Every check is wrapped so a single failure never breaks the scan. Keep it that way.
