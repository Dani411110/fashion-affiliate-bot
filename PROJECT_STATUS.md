# Fashion Bot Status

Generated: 2026-06-07T02:56:33

## Database
- SQLite path: `C:\Users\User\Desktop\proiect reddit\fashion-affiliate-bot\data\fashion_bot.db`
- Products cached: 170
- Pinterest images: 10/12 unused/total
- Posts total: 2

## Platform Toggles
- Reddit: OFF (missing credentials)
- Instagram: OFF (missing credentials)
- TikTok: OFF (missing credentials)
- YouTube: OFF (configured)

## Next Manual Steps
- Add Drive folder IDs to Railway Variables if they are not already set.
- YouTube: finish OAuth and copy `data/youtube_token.json`/client secrets into Railway strategy before enabling.
- TikTok: wait for review, then run `python main.py tiktok-auth-url` and `python main.py tiktok-exchange-code <code>`.
- Instagram: wait for Meta pending role/review, then add token/user id.
- Reddit: wait for API approval, then add credentials.
- Keep platform toggles OFF until each platform passes `python main.py platform-test --live`.
