# Prompt pentru continuare in alt chat Codex

Continua dezvoltarea proiectului Fashion Affiliate Content Automation Bot.

Workspace:
`C:\Users\User\Desktop\proiect reddit\fashion-affiliate-bot`

Context actual:
- Repo GitHub privat: `Dani411110/fashion-affiliate-bot`
- Railway production URL: `https://fashion-affiliate-bot-production.up.railway.app`
- Railway service este online, Telegram polling pornit.
- SQLite cloud este pe volum persistent: `/data/fashion_bot.db`.
- Debug dashboard:
  - `/`
  - `/api/status`
  - `/api/readiness`
  - `/api/logs`
  - `/health`
- Ultimul commit important: `be32f24 Add overnight readiness and ops helpers`

Ce s-a facut deja:
- GitHub privat creat si codul impins pe `main`.
- Railway deploy functional.
- Variabile principale Railway puse, botul Telegram online.
- Volum Railway atasat la `/data`, `SQLITE_PATH=/data/fashion_bot.db`.
- Full/debug dashboard adaugat.
- Endpoint readiness adaugat.
- Telegram are `.start`, `.status`, `.queue`, `.platforms`, `.readiness`, `.doctor`, `.scrape`, `.scrapeproducts`, `.postqueue`.
- Flow Telegram: categorie -> alegere 5/6/7/8 poze -> preview album -> approve/reject/regenerate.
- Mulebuy cloud cache: peste 500 produse.
- Pinterest local: 12 total, 10 unused dupa scrape.
- Google Drive folder IDs au fost create local:
  - `DRIVE_FOLDER_QUEUE_ID=1XQxpt404MtmxtGT1ZgBuvu64H2SODqst`
  - `DRIVE_FOLDER_POSTED_ID=1GLJHvU6iI5Ejt0yiKxK-YZw0nL3Fcq59`
  - `DRIVE_FOLDER_REJECTED_ID=1W4Nu-aLIBru_nl63gjT3UGppzuUn1RE0`
  - `DRIVE_FOLDER_RAW_PINTEREST_ID=1HWjWIjFo1maNHjggITqaZazK2SUMITQx`
- Aceste folder IDs sunt puse in `.env` local, dar trebuie verificate/adaugate si in Railway Variables.
- TikTok app `FashionBot` este in review.
- TikTok endpoints publice:
  - `/terms`
  - `/privacy`
  - `/tiktok/callback`
  - `/tiktok/demo`
  - verificare URL prefix servita.
- TikTok OAuth helpers:
  - `python main.py tiktok-auth-url`
  - `python main.py tiktok-exchange-code <code>`
- YouTube OAuth client secret este local in `config/youtube_client_secret.json`, ignorat de Git.
- YouTube helper commands:
  - `python main.py youtube-auth-url`
  - `python main.py youtube-exchange-code <code>`
- `http://localhost:8081/` a fost autorizat in Google OAuth client de user.
- Google OAuth a fost blocat initial de access_denied; user a dat publish pe consent screen, dar tokenul inca nu a fost obtinut.

Status validari:
- `python -m py_compile` pe toate fisierele Python: OK
- `python main.py doctor --live`: OK pentru OpenAI; YouTube client secrets prezent; platformele OFF.
- `python main.py platform-test --live`: YouTube client secrets OK, OAuth token missing.
- Railway `/api/readiness`: live si arata blocajele.

Ce trebuie facut urmator:
1. Adauga/verifica in Railway Variables folder IDs Drive de mai sus.
2. In Railway Variables adauga TikTok client key/secret/redirect daca nu exista:
   - `TIKTOK_CLIENT_KEY`
   - `TIKTOK_CLIENT_SECRET`
   - `TIKTOK_REDIRECT_URI=https://fashion-affiliate-bot-production.up.railway.app/tiktok/callback`
   - pastreaza `ENABLE_TIKTOK=false` pana avem token.
3. YouTube:
   - Ruleaza `python main.py youtube-auth-url`.
   - Deschide URL-ul, accepta, copiaza `code` din URL-ul localhost.
   - Ruleaza `python main.py youtube-exchange-code <code>`.
   - Dupa ce exista `data/youtube_token.json`, decide strategia pentru Railway token/secrets si abia apoi `ENABLE_YOUTUBE=true`.
4. TikTok:
   - Asteapta review.
   - Dupa aprobare, ruleaza `python main.py tiktok-auth-url`.
   - Schimba callback `code` cu `python main.py tiktok-exchange-code <code>`.
   - Pune `TIKTOK_ACCESS_TOKEN` si `TIKTOK_REFRESH_TOKEN` in Railway.
5. Instagram:
   - Este pending in Meta.
   - Dupa acceptare/approval, obtine `INSTAGRAM_ACCESS_TOKEN` si `INSTAGRAM_USER_ID`.
6. Reddit:
   - Asteapta API approval.
   - Pune `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, username, password, user agent.
7. Nu activa nicio platforma pana nu trece `python main.py platform-test --live`.

Atentie:
- Nu comite `.env`, `config/service_account.json`, `config/youtube_client_secret.json`, `data/youtube_token.json`, `data/tiktok_cookies.json`.
- TikTok client secret a fost vizibil in screenshot; dupa finalizarea setup-ului trebuie regenerat si pus din nou in Railway.
- User prefera romana, raspunsuri directe, fara pierdut timp.
