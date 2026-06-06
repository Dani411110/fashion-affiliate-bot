# Fashion Affiliate Content Automation Bot

A modular Python CLI that scrapes outfit inspiration from Pinterest, pairs it with affiliate products from a Google Sheet, generates AI captions, creates vertical video slideshows, and posts to Reddit, TikTok, Instagram, and YouTube Shorts — 3 times a day with human-in-the-loop approval.

---

## 1. Project Overview

**Flow:**
1. Pinterest scraper grabs outfit inspiration images (filtered by GPT-4o Vision)
2. Google Sheet provides product images + Mulebuy affiliate links
3. GPT-4o generates platform-specific captions + hashtags
4. moviepy/ffmpeg builds a 9:16 video slideshow with music
5. You review each post in the terminal (approve / reject / regenerate)
6. Approved posts are published to Reddit, TikTok, Instagram, YouTube Shorts

**4 post categories:**
- Complete Outfit with Accessories
- Random Finds
- Most Popular (sorted by popularity score)
- Cheapest Finds (sorted by price)

---

## 2. Prerequisites

- **Python 3.11+**
- **ffmpeg** installed system-wide and on PATH
  - Windows: `winget install ffmpeg` or download from https://ffmpeg.org/download.html
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`
- **Playwright browsers** (installed during setup)
- A Google Cloud project with the following APIs enabled:
  - Google Sheets API
  - Google Drive API
  - YouTube Data API v3

---

## 3. Installation

```bash
git clone <your-repo>
cd fashion-affiliate-bot

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt

# Install Playwright browsers
python -m playwright install chromium

# Copy and fill in environment variables
cp .env.example .env
```

---

## 4. Google Cloud Setup

1. Go to https://console.cloud.google.com
2. Create a new project (or use an existing one)
3. Enable these APIs:
   - **Google Sheets API**
   - **Google Drive API**
4. Create a **Service Account**:
   - IAM & Admin → Service Accounts → Create
   - Download the JSON key file
   - Set `GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/key.json` in `.env`
5. **Share your Google Sheet** with the service account email (e.g. `bot@project.iam.gserviceaccount.com`) as Editor
6. **Share your Google Drive folder** with the service account as Editor

---

## 5. YouTube OAuth Setup

YouTube uses OAuth 2.0 (not service account) because it uploads on behalf of a real user account.

1. In Google Cloud Console → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (type: Desktop App)
3. Download the JSON and set `YOUTUBE_CLIENT_SECRETS_JSON=/path/to/secrets.json`
4. On first run, a browser window opens for consent
5. The token is saved to `data/youtube_token.json` for subsequent runs

---

## 6. Google Sheet Structure

Tab name must match `GOOGLE_SHEET_TAB_NAME` (default: `Products`).

| Column | Header | Example |
|--------|--------|---------|
| A | image_url | https://i.ibb.co/abc123/shirt.jpg |
| B | mulebuy_link | https://mulebuy.com/item/12345 |
| C | category | tops |
| D | price | 29.99 |
| E | name | Oversized Linen Shirt |
| F | tags | streetwear,oversized,summer |
| G | popularity_score | 85 |

**Valid categories:** `tops`, `bottoms`, `shoes`, `accessories`, `bags`

---

## 7. TikTok Cookie Export

TikTok requires browser cookies (no official API for posting).

1. Install the **EditThisCookie** or **Cookie-Editor** browser extension
2. Log in to TikTok in your browser
3. Export cookies as **JSON** and save to `data/tiktok_cookies.json`
4. Set `TIKTOK_COOKIES_PATH=data/tiktok_cookies.json` in `.env`

> Cookies expire periodically. Re-export when TikTok uploads start failing.

---

## 8. First Run Walkthrough

```bash
# Step 1: Verify all credentials and create Drive folders
python main.py setup

# Step 2: Scrape your first batch of Pinterest images
python main.py scrape --count 20

# Step 3: Run the daily session
python main.py run
```

During `run`:
1. You'll see today's 3 posting slots
2. For each post, pick a category (1-4)
3. The bot builds the post (scrapes if needed, generates captions, creates video)
4. Review the post in the terminal
5. Press `y` to approve, `n` to reject, `r` to regenerate captions
6. After all 3 posts are reviewed, choose to post now or save to queue

---

## 9. All CLI Commands

```bash
# Full daily session (build + review + publish)
python main.py run

# Scrape Pinterest images only
python main.py scrape
python main.py scrape --count 30
python main.py scrape --keywords "y2k fashion,aesthetic outfit"

# Publish all queued approved posts immediately
python main.py post-queue

# Force-sync Google Sheet to local SQLite cache
python main.py sync-sheet

# First-time setup and credential verification
python main.py setup

# Show database statistics
python main.py status
```

---

## 10. Music Folder Setup

Place royalty-free MP3 files in `data/music/`. Name them with the mood in the filename:

```
data/music/
├── upbeat_fashion_track1.mp3
├── upbeat_trendy_mix.mp3
├── chill_lofi_background.mp3
├── dramatic_cinematic1.mp3
└── trendy_pop_beat.mp3
```

Moods detected by filename: `upbeat`, `chill`, `dramatic`, `trendy`.

If no local files are found, the bot downloads from Pixabay (requires `PIXABAY_API_KEY`).
If Pixabay is unavailable, a 60-second silent audio track is generated via ffmpeg.

**Good sources for royalty-free music:**
- https://pixabay.com/music/
- https://www.bensound.com/
- https://freemusicarchive.org/

---

## 11. Railway Deployment

Railway can run the bot with SQLite if you attach a persistent volume to the
service before production use.

1. Push this repository to a private GitHub repo.
2. In Railway, create a new project from the GitHub repo.
3. Add the environment variables from `.env` in the Railway dashboard.
4. Add a persistent volume to the service. Railway injects
   `RAILWAY_VOLUME_MOUNT_PATH` automatically.
5. Leave `SQLITE_PATH` unset, or set it explicitly to
   `$RAILWAY_VOLUME_MOUNT_PATH/fashion_bot.db`.
6. Keep `ENABLE_REDDIT`, `ENABLE_INSTAGRAM`, `ENABLE_TIKTOK`, and
   `ENABLE_YOUTUBE` disabled until each platform credential is ready.

Local development still defaults to `data/fashion_bot.db`.

---

## 12. Phase 2 Migration Guide

The codebase is pre-structured for a server + Telegram bot migration. Every file that changes has a `# PHASE 2 MIGRATION:` comment block describing the swap.

### Files to swap

| Current | Phase 2 replacement |
|---------|---------------------|
| `core/scheduler.py` → `run_daily_session()` | Telegram `/run` command handler |
| `core/approval_interface.py` → `CLIApprovalInterface` | `TelegramApprovalInterface` with inline keyboard |
| `database/sqlite_db.py` → `SqliteDatabase` | `SupabaseDatabase` implementing `BaseDatabase` |
| `config/settings.py` → `.env` file | Remote config (Supabase secrets or bot `/config` command) |

### What to implement

1. **BaseDatabase interface** (already noted in `database/sqlite_db.py`):
   ```python
   class BaseDatabase(ABC):
       @abstractmethod
       def create_post(...): ...
       # All other public methods
   ```

2. **TelegramApprovalInterface**:
   - Same `review_post(post_package) -> bool` signature
   - Sends preview image + formatted text to operator chat
   - Returns bool via `asyncio.Queue` waited on by `PostBuilder`

3. **Scheduler → Telegram bot commands**:
   - `/run` → `Scheduler.run_daily_session()`
   - `/status` → `Scheduler.get_status()`
   - `/postqueue` → `Scheduler.post_scheduled()`

4. **APScheduler** for automatic posting at configured times

5. **Deploy to Railway / Fly.io / VPS** - use a Railway volume for SQLite, or migrate to Supabase for a fully managed production database

---

## Project Structure

```
fashion-affiliate-bot/
├── main.py                    CLI entry point
├── requirements.txt
├── .env.example
├── config/settings.py         All config from .env
├── core/
│   ├── post_builder.py        Central orchestrator
│   ├── approval_interface.py  CLI review flow
│   └── scheduler.py           Daily session + queue publishing
├── scrapers/pinterest_scraper.py
├── filters/image_filter.py    GPT-4o Vision quality check
├── sheets/google_sheets.py    Product data source
├── drive/google_drive.py      Asset storage
├── database/sqlite_db.py      Local persistence
├── video/slideshow_creator.py moviepy + ffmpeg
├── music/music_provider.py
├── publishers/
│   ├── reddit_publisher.py
│   ├── tiktok_publisher.py
│   ├── instagram_publisher.py
│   └── youtube_publisher.py
├── captions/caption_generator.py
├── categories/content_categories.py
└── data/
    ├── music/                 Local MP3 files
    ├── temp/                  Downloaded images + rendered videos
    └── logs/                  Rotating daily log files
```
