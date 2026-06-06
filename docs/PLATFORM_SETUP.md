# Platform Setup Notes

## Reddit

Keep `ENABLE_REDDIT=false` until API approval and credentials are ready.

Required env:

```env
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
REDDIT_PASSWORD=
REDDIT_USER_AGENT=FashionAffiliateBot/1.0 by u/YOUR_USERNAME
REDDIT_SUBREDDIT=
ENABLE_REDDIT=true
```

Before enabling:

- Confirm the target subreddit allows affiliate links.
- Prefer posting as a gallery with the affiliate links in a comment.
- Run `python main.py doctor --live` locally after credentials are added.

## Instagram

Requires Instagram Business/Creator account connected to a Facebook Page and Graph API access.

Required env:

```env
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_USER_ID=
DRIVE_FOLDER_QUEUE_ID=
ENABLE_INSTAGRAM=true
```

Notes:

- Carousel publishing needs public image URLs.
- This bot uploads images to Google Drive and uses direct download URLs.
- Use a long-lived access token for stable Railway operation.

## TikTok

Preferred cloud path is the Content Posting API token:

```env
TIKTOK_ACCESS_TOKEN=
ENABLE_TIKTOK=true
```

Browser cookies are a local fallback only:

```env
TIKTOK_COOKIES_PATH=data/tiktok_cookies.json
```

Railway/headless browser cookie uploads are fragile. Prefer official API access.

## YouTube Shorts

YouTube uploads need OAuth as a real channel owner.

Required env:

```env
YOUTUBE_CLIENT_SECRETS_JSON=
ENABLE_YOUTUBE=true
```

Current code can build a simple video from carousel images with ffmpeg. The OAuth token path should be made cloud-safe before fully unattended Railway uploads.
