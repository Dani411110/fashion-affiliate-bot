# Fashion Bot Deployment

## 1. GitHub

1. Create a private GitHub repository named `fashion-affiliate-bot`.
2. Add it as the remote:

```powershell
git remote add origin https://github.com/YOUR_USER/fashion-affiliate-bot.git
git push -u origin main
```

Do not commit `.env`, service account JSON files, SQLite DBs, cookies, or OAuth tokens.

## 2. Railway

1. Railway -> New Project -> Deploy from GitHub repo.
2. Railway should detect `Dockerfile`.
3. Add a persistent volume before production use.
4. Leave `SQLITE_PATH` unset unless you want an explicit path. The app uses:

```text
$RAILWAY_VOLUME_MOUNT_PATH/fashion_bot.db
```

5. Add all environment variables from `.env`.
6. Start with all platform toggles disabled:

```env
ENABLE_REDDIT=false
ENABLE_INSTAGRAM=false
ENABLE_TIKTOK=false
ENABLE_YOUTUBE=false
```

7. After deploy, message the Telegram bot:

```text
.status
.platforms
.syncsheet
```

## 3. Railway Environment Checklist

Required:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_SHEET_ID=
GOOGLE_SHEET_TAB_NAME=Products
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
POST_TIME_1=09:00
POST_TIME_2=14:00
POST_TIME_3=19:00
ENABLE_REDDIT=false
ENABLE_INSTAGRAM=false
ENABLE_TIKTOK=false
ENABLE_YOUTUBE=false
```

Google Drive, once folders exist:

```env
DRIVE_FOLDER_QUEUE_ID=
DRIVE_FOLDER_POSTED_ID=
DRIVE_FOLDER_REJECTED_ID=
DRIVE_FOLDER_RAW_PINTEREST_ID=
```

Platform credentials, add only when ready:

```env
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
REDDIT_PASSWORD=
REDDIT_USER_AGENT=
REDDIT_SUBREDDIT=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_USER_ID=
TIKTOK_ACCESS_TOKEN=
YOUTUBE_CLIENT_SECRETS_JSON=
```

Local-only:

```env
NOTION_TOKEN=
NOTION_PAGE_ID=
TIKTOK_COOKIES_PATH=data/tiktok_cookies.json
```

## 4. Smoke Tests

Local:

```powershell
$env:PYTHONIOENCODING="utf-8"
python main.py doctor
python main.py status
docker build -t fashion-affiliate-bot:local .
```

Cloud:

```text
.status
.platforms
.syncsheet
.start
```

## 5. Known Production Notes

- SQLite is safe on Railway only when a persistent volume is attached.
- `postqueue` needs posts built after the package persistence migration to have full image/caption data.
- TikTok browser-cookie upload is fragile on headless cloud servers. Prefer `TIKTOK_ACCESS_TOKEN`.
- YouTube OAuth is easiest to authorize locally first, then move token storage to a cloud-safe flow later.
