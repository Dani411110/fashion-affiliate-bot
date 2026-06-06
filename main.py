"""CLI entry point for the Fashion Affiliate Content Automation Bot."""

import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import click
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def _platform_rows(settings):
    return [
        ("Reddit", settings.enable_reddit, bool(settings.reddit_client_id)),
        ("Instagram", settings.enable_instagram, bool(settings.instagram_access_token and settings.instagram_user_id)),
        ("TikTok", settings.enable_tiktok, bool(settings.tiktok_access_token or Path(settings.tiktok_cookies_path).exists())),
        ("YouTube", settings.enable_youtube, bool(settings.youtube_client_secrets_json)),
    ]


@click.group()
def cli():
    """Fashion Affiliate Content Automation Bot."""


@cli.command()
def run():
    """Run the full daily session: build → review → publish 3 posts."""
    from core.scheduler import Scheduler
    Scheduler().run_daily_session()


@cli.command()
@click.option(
    "--count", default=15, show_default=True, help="Number of images to scrape."
)
@click.option(
    "--keywords",
    default=None,
    help="Comma-separated keywords (overrides .env PINTEREST_KEYWORDS).",
)
def scrape(count: int, keywords):
    """Scrape Pinterest outfit inspiration images."""
    from scrapers.pinterest_scraper import scrape_batch
    kws = [k.strip() for k in keywords.split(",")] if keywords else None
    console.print(f"[cyan]Scraping {count} images…[/cyan]")
    total = scrape_batch(keywords=kws, target_count=count)
    console.print(f"[green]Done. Saved {total} new image(s).[/green]")


@cli.command("post-queue")
def post_queue():
    """Publish all approved posts currently in the queue."""
    from core.scheduler import Scheduler
    Scheduler().post_scheduled()


@cli.command("sync-sheet")
def sync_sheet():
    """Force-sync the Google Sheet products to the local SQLite cache."""
    from sheets.google_sheets import get_sheets_client
    from database.sqlite_db import get_db
    console.print("[cyan]Syncing Google Sheet…[/cyan]")
    products = get_sheets_client().get_all_products(force_refresh=True)
    get_db().sync_products(products)
    console.print(f"[green]Synced {len(products)} products.[/green]")


@cli.command("scrape-products")
@click.option(
    "--categories",
    default=None,
    help="Comma-separated categories: Jackets,T-shirts,Shoes,Hoodies,Pants,Bags & Wallets,Accessories",
)
@click.option("--per-category", default=30, show_default=True, help="Max products per category.")
def scrape_products(categories, per_category):
    """Scrape products from mulebuy.gg and save to local cache."""
    from scrapers.mulebuy_scraper import scrape_mulebuy, CATEGORIES
    cats = [c.strip() for c in categories.split(",")] if categories else None
    if cats:
        invalid = [c for c in cats if c not in CATEGORIES]
        if invalid:
            console.print(f"[red]Unknown categories: {invalid}[/red]")
            console.print(f"Valid: {list(CATEGORIES.keys())}")
            return
    console.print(f"[cyan]Scraping Mulebuy products ({per_category} per category)…[/cyan]")
    products = scrape_mulebuy(categories=cats, per_category=per_category)
    console.print(f"[green]Done. Scraped {len(products)} products.[/green]")


@cli.command("scrape-products-all")
@click.option(
    "--categories",
    default=None,
    help="Comma-separated categories. Default: all Mulebuy categories.",
)
@click.option("--category-timeout", default=300, show_default=True, help="Seconds before skipping a stuck category.")
@click.option("--scrolls", default=30, show_default=True, help="Scroll passes per category before collecting cards.")
def scrape_products_all(categories, category_timeout, scrolls):
    """Scrape every visible Mulebuy product from every category into SQLite."""
    from scrapers.mulebuy_scraper import scrape_mulebuy_all, CATEGORIES
    cats = [c.strip() for c in categories.split(",")] if categories else None
    if cats:
        invalid = [c for c in cats if c not in CATEGORIES]
        if invalid:
            console.print(f"[red]Unknown categories: {invalid}[/red]")
            console.print(f"Valid: {list(CATEGORIES.keys())}")
            return
    console.print("[cyan]Scraping all visible Mulebuy products into SQLite...[/cyan]")
    console.print("[dim]This is a long-running Railway/ops command, not a Telegram command.[/dim]")
    products = scrape_mulebuy_all(
        categories=cats,
        category_timeout=category_timeout,
        scrolls=scrolls,
    )
    console.print(f"[green]Done. Scraped {len(products)} products.[/green]")


@cli.command()
@click.option("--live", is_flag=True, help="Also perform live API checks where safe.")
def doctor(live: bool):
    """Run deployment-readiness checks without mutating content."""
    from config.settings import get_settings
    from database.sqlite_db import get_db

    s = get_settings()
    db = get_db()
    stats = db.get_stats()

    console.print("\n[bold cyan]Fashion Bot Doctor[/bold cyan]\n")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Check", style="bold cyan")
    table.add_column("Status")
    table.add_column("Details")

    railway_volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
    table.add_row("SQLite path", "OK", str(s.sqlite_path))
    table.add_row("Railway volume", "ON" if railway_volume else "OFF", railway_volume or "not detected")
    table.add_row("DB stats", "OK", f"{stats['products_cached']} products, {stats['pinterest_unused']}/{stats['pinterest_total']} Pinterest unused")
    table.add_row("Dockerfile", "OK" if Path("Dockerfile").exists() else "MISSING", "Docker build entrypoint")
    table.add_row("railway.toml", "OK" if Path("railway.toml").exists() else "MISSING", "Railway config")
    table.add_row(".dockerignore", "OK" if Path(".dockerignore").exists() else "MISSING", "Protects secrets from image context")

    required = [
        ("OPENAI_API_KEY", s.openai_api_key),
        ("GOOGLE_SERVICE_ACCOUNT_JSON", s.google_service_account_json),
        ("GOOGLE_SHEET_ID", s.google_sheet_id),
        ("TELEGRAM_BOT_TOKEN", s.telegram_bot_token),
        ("TELEGRAM_CHAT_ID", str(s.telegram_chat_id)),
    ]
    for key, value in required:
        table.add_row(key, "OK" if value else "MISSING", "set" if value else "empty")

    for name, enabled, configured in _platform_rows(s):
        status_text = "ON" if enabled else "OFF"
        detail = "configured" if configured else "missing credentials"
        table.add_row(name, status_text, detail)

    if live:
        try:
            import openai
            openai.OpenAI(api_key=s.openai_api_key).models.list()
            table.add_row("OpenAI live", "OK", "models.list succeeded")
        except Exception as exc:
            table.add_row("OpenAI live", "FAIL", str(exc)[:120])

    console.print(table)


@cli.command("platform-test")
@click.option("--live", is_flag=True, help="Run safe live auth checks. Never publishes content.")
def platform_test(live: bool):
    """Check platform publishing readiness without posting anything."""
    from config.settings import get_settings

    s = get_settings()
    console.print("\n[bold cyan]Platform Readiness[/bold cyan]\n")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Platform", style="bold cyan")
    table.add_column("Toggle")
    table.add_column("Config")
    table.add_column("Safe test")
    table.add_column("Next action")

    def onoff(value: bool) -> str:
        return "[green]ON[/green]" if value else "[yellow]OFF[/yellow]"

    def ok(value: bool) -> str:
        return "[green]OK[/green]" if value else "[red]MISSING[/red]"

    reddit_ready = all([
        s.reddit_client_id,
        s.reddit_client_secret,
        s.reddit_username,
        s.reddit_password,
        s.reddit_subreddit,
    ])
    reddit_test = "not run"
    if live and reddit_ready:
        try:
            import praw
            reddit = praw.Reddit(
                client_id=s.reddit_client_id,
                client_secret=s.reddit_client_secret,
                username=s.reddit_username,
                password=s.reddit_password,
                user_agent=s.reddit_user_agent,
            )
            me = reddit.user.me()
            subreddit = reddit.subreddit(s.reddit_subreddit)
            _ = subreddit.display_name
            reddit_test = f"auth OK as u/{me}"
        except Exception as exc:
            reddit_test = f"FAIL: {str(exc)[:90]}"
    table.add_row(
        "Reddit",
        onoff(s.enable_reddit),
        ok(reddit_ready),
        reddit_test,
        "Add Reddit app client_id/client_secret, then ENABLE_REDDIT=true" if not reddit_ready else "Ready for approval publish test",
    )

    instagram_ready = bool(s.instagram_access_token and s.instagram_user_id)
    drive_ready = bool(s.drive_folder_queue_id)
    instagram_test = "not run"
    if live and instagram_ready:
        try:
            import requests
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{s.instagram_user_id}",
                params={
                    "fields": "id,username,account_type",
                    "access_token": s.instagram_access_token,
                },
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            instagram_test = f"auth OK @{payload.get('username', payload.get('id', 'unknown'))}"
        except Exception as exc:
            instagram_test = f"FAIL: {str(exc)[:90]}"
    table.add_row(
        "Instagram",
        onoff(s.enable_instagram),
        ok(instagram_ready and drive_ready),
        instagram_test,
        "Add IG token/user id + Drive queue folder" if not (instagram_ready and drive_ready) else "Ready for carousel publish test",
    )

    cookies_path = Path(s.tiktok_cookies_path)
    cookies_ready = cookies_path.exists()
    tiktok_oauth_ready = bool(s.tiktok_client_key and s.tiktok_client_secret and s.tiktok_redirect_uri)
    tiktok_ready = bool(s.tiktok_access_token or cookies_ready)
    tiktok_test = "not run"
    if live and s.tiktok_access_token:
        try:
            import requests
            resp = requests.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {s.tiktok_access_token}"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error", {}).get("code") not in (None, "ok"):
                raise RuntimeError(data.get("error", {}).get("message", data))
            tiktok_test = "API token OK"
        except Exception as exc:
            tiktok_test = f"FAIL: {str(exc)[:90]}"
    elif live and cookies_ready:
        try:
            import json
            raw = json.loads(cookies_path.read_text(encoding="utf-8"))
            tiktok_test = f"cookies readable ({len(raw)} entries)"
        except Exception as exc:
            tiktok_test = f"FAIL: {str(exc)[:90]}"
    table.add_row(
        "TikTok",
        onoff(s.enable_tiktok),
        ok(tiktok_ready or tiktok_oauth_ready),
        tiktok_test,
        "Run tiktok-auth-url after app review, then exchange callback code" if not s.tiktok_access_token else "Ready for API publish test",
    )

    youtube_ready = bool(s.youtube_client_secrets_json)
    token_path = Path("data/youtube_token.json")
    youtube_test = "not run"
    if live and youtube_ready:
        secrets_path = Path(s.youtube_client_secrets_json)
        if secrets_path.exists() or s.youtube_client_secrets_json.strip().startswith("{"):
            youtube_test = "client secrets present"
            if token_path.exists():
                youtube_test += ", OAuth token present"
            else:
                youtube_test += ", OAuth token missing"
        else:
            youtube_test = "FAIL: client secrets path not found"
    table.add_row(
        "YouTube",
        onoff(s.enable_youtube),
        ok(youtube_ready),
        youtube_test,
        "Add OAuth client secrets and run first OAuth locally" if not youtube_ready else "Run OAuth locally before Railway publish",
    )

    console.print(table)
    console.print("\n[dim]This command does not publish or modify posts.[/dim]")


@cli.command("tiktok-auth-url")
@click.option(
    "--scopes",
    default="user.info.basic,video.upload,video.publish",
    show_default=True,
    help="Comma-separated TikTok OAuth scopes.",
)
def tiktok_auth_url(scopes: str):
    """Print the TikTok OAuth URL for generating a user authorization code."""
    from config.settings import get_settings

    s = get_settings()
    if not s.tiktok_client_key:
        console.print("[red]TIKTOK_CLIENT_KEY is missing.[/red]")
        return
    scope_value = ",".join(part.strip() for part in scopes.split(",") if part.strip())
    params = {
        "client_key": s.tiktok_client_key,
        "scope": scope_value,
        "response_type": "code",
        "redirect_uri": s.tiktok_redirect_uri,
        "state": secrets.token_urlsafe(16),
    }
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)
    console.print("[green]Open this URL after TikTok approves the app:[/green]")
    console.print(url)


@cli.command("tiktok-exchange-code")
@click.argument("code")
def tiktok_exchange_code(code: str):
    """Exchange a TikTok callback authorization code for access/refresh tokens."""
    from config.settings import get_settings
    import requests

    s = get_settings()
    missing = [
        name for name, value in [
            ("TIKTOK_CLIENT_KEY", s.tiktok_client_key),
            ("TIKTOK_CLIENT_SECRET", s.tiktok_client_secret),
            ("TIKTOK_REDIRECT_URI", s.tiktok_redirect_uri),
        ]
        if not value
    ]
    if missing:
        console.print(f"[red]Missing env vars:[/red] {', '.join(missing)}")
        return

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": s.tiktok_client_key,
            "client_secret": s.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": s.tiktok_redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        payload = resp.json()
    except ValueError:
        console.print(f"[red]TikTok returned non-JSON response:[/red] {resp.text[:300]}")
        return

    out = Path("data/tiktok_token_response.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if resp.ok and payload.get("access_token"):
        console.print(f"[green]Token response saved to:[/green] {out}")
        console.print("[yellow]Copy access_token to Railway as TIKTOK_ACCESS_TOKEN.")
        console.print("Copy refresh_token to Railway as TIKTOK_REFRESH_TOKEN.[/yellow]")
    else:
        console.print(f"[red]Token exchange failed:[/red] {payload}")


@cli.command("youtube-auth-url")
def youtube_auth_url():
    """Print a YouTube OAuth URL without starting a local callback server."""
    from config.settings import get_settings
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    if not s.youtube_client_secrets_json:
        console.print("[red]YOUTUBE_CLIENT_SECRETS_JSON is missing.[/red]")
        return

    secrets_path = s.youtube_client_secrets_json
    if not Path(secrets_path).exists():
        tmp = Path("data/youtube_client_secrets.json")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(s.youtube_client_secrets_json, encoding="utf-8")
        secrets_path = str(tmp)

    redirect_uri = f"http://localhost:{os.getenv('YOUTUBE_OAUTH_PORT', '8081')}/"
    flow = Flow.from_client_secrets_file(
        secrets_path,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        redirect_uri=redirect_uri,
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    console.print("[green]Open this URL, approve YouTube upload, then copy the 'code' from the localhost URL:[/green]")
    console.print(url)


@cli.command("youtube-exchange-code")
@click.argument("code")
def youtube_exchange_code(code: str):
    """Exchange a copied Google OAuth code for data/youtube_token.json."""
    from config.settings import get_settings
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    if not s.youtube_client_secrets_json:
        console.print("[red]YOUTUBE_CLIENT_SECRETS_JSON is missing.[/red]")
        return

    secrets_path = s.youtube_client_secrets_json
    if not Path(secrets_path).exists():
        tmp = Path("data/youtube_client_secrets.json")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(s.youtube_client_secrets_json, encoding="utf-8")
        secrets_path = str(tmp)

    redirect_uri = f"http://localhost:{os.getenv('YOUTUBE_OAUTH_PORT', '8081')}/"
    flow = Flow.from_client_secrets_file(
        secrets_path,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        redirect_uri=redirect_uri,
    )
    flow.fetch_token(code=code)
    token_path = Path("data/youtube_token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
    console.print(f"[green]YouTube OAuth token saved:[/green] {token_path}")


@cli.command("drive-folders")
def drive_folders():
    """Create/check Google Drive folders and print Railway env values."""
    from drive.google_drive import get_drive_client

    try:
        folder_ids = get_drive_client().ensure_folder_structure()
    except Exception as exc:
        console.print(f"[red]Drive folder setup failed:[/red] {exc}")
        console.print("[yellow]Make sure the service account has access to Google Drive.[/yellow]")
        return

    console.print("[green]Drive folders ready. Add these to Railway Variables:[/green]")
    mapping = {
        "DRIVE_FOLDER_QUEUE_ID": folder_ids.get("queue", ""),
        "DRIVE_FOLDER_POSTED_ID": folder_ids.get("posted", ""),
        "DRIVE_FOLDER_REJECTED_ID": folder_ids.get("rejected", ""),
        "DRIVE_FOLDER_RAW_PINTEREST_ID": folder_ids.get("raw_pinterest", ""),
    }
    for key, value in mapping.items():
        console.print(f"{key}={value}")


@cli.command("backup-db")
@click.option("--dest", default=None, help="Destination .db path. Defaults to data/backups timestamp.")
def backup_db(dest):
    """Backup the SQLite database file."""
    from database.sqlite_db import get_db

    target = Path(dest) if dest else Path("data/backups") / f"fashion_bot_{datetime.now():%Y%m%d_%H%M%S}.db"
    out = get_db().backup_to(target)
    console.print(f"[green]DB backup written:[/green] {out}")


@cli.command("restore-db")
@click.argument("source")
def restore_db(source):
    """Restore SQLite from a backup path."""
    from database.sqlite_db import get_db

    get_db().restore_from(Path(source))
    console.print(f"[green]DB restored from:[/green] {source}")


@cli.command("cleanup-temp")
@click.option("--days", default=3, show_default=True, help="Delete temp files older than this many days.")
def cleanup_temp(days: int):
    """Delete old temp files without touching the SQLite DB."""
    from config.settings import get_settings

    cutoff = datetime.now().timestamp() - (days * 86400)
    root = get_settings().temp_folder
    removed = 0
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    console.print(f"[green]Removed {removed} old temp file(s).[/green]")


@cli.command("write-status-report")
def write_status_report():
    """Write a local PROJECT_STATUS.md snapshot."""
    from config.settings import get_settings
    from database.sqlite_db import get_db

    s = get_settings()
    stats = get_db().get_stats()
    lines = [
        "# Fashion Bot Status",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Database",
        f"- SQLite path: `{s.sqlite_path}`",
        f"- Products cached: {stats['products_cached']}",
        f"- Pinterest images: {stats['pinterest_unused']}/{stats['pinterest_total']} unused/total",
        f"- Posts total: {stats['posts_total']}",
        "",
        "## Platform Toggles",
    ]
    for name, enabled, configured in _platform_rows(s):
        lines.append(f"- {name}: {'ON' if enabled else 'OFF'} ({'configured' if configured else 'missing credentials'})")
    lines += [
        "",
        "## Next Manual Steps",
        "- Add Drive folder IDs to Railway Variables if they are not already set.",
        "- YouTube: finish OAuth and copy `data/youtube_token.json`/client secrets into Railway strategy before enabling.",
        "- TikTok: wait for review, then run `python main.py tiktok-auth-url` and `python main.py tiktok-exchange-code <code>`.",
        "- Instagram: wait for Meta pending role/review, then add token/user id.",
        "- Reddit: wait for API approval, then add credentials.",
        "- Keep platform toggles OFF until each platform passes `python main.py platform-test --live`.",
    ]
    Path("PROJECT_STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print("[green]Wrote PROJECT_STATUS.md[/green]")


@cli.command()
def setup():
    """First-time setup: verify credentials and create Google Drive folder structure."""
    console.print("[bold cyan]Fashion Affiliate Bot — First-time Setup[/bold cyan]\n")
    errors = []

    # 1. Settings / .env
    try:
        from config.settings import get_settings
        s = get_settings()
        console.print("[green]✓[/green] .env loaded")
    except EnvironmentError as exc:
        console.print(f"[red]✗ .env error:[/red] {exc}")
        errors.append(str(exc))

    # 2. Google Sheets
    try:
        from sheets.google_sheets import get_sheets_client
        products = get_sheets_client().get_all_products()
        console.print(f"[green]✓[/green] Google Sheets: {len(products)} products")
    except Exception as exc:
        console.print(f"[red]✗ Google Sheets:[/red] {exc}")
        errors.append(str(exc))

    # 3. Google Drive
    try:
        from drive.google_drive import get_drive_client
        folder_ids = get_drive_client().ensure_folder_structure()
        console.print(f"[green]✓[/green] Google Drive folders: {list(folder_ids.keys())}")
    except Exception as exc:
        console.print(f"[red]✗ Google Drive:[/red] {exc}")
        errors.append(str(exc))

    # 4. SQLite
    try:
        from database.sqlite_db import get_db
        stats = get_db().get_stats()
        console.print(f"[green]✓[/green] SQLite DB: {stats}")
    except Exception as exc:
        console.print(f"[red]✗ SQLite:[/red] {exc}")
        errors.append(str(exc))

    # 5. OpenAI
    try:
        import openai
        from config.settings import get_settings
        client = openai.OpenAI(api_key=get_settings().openai_api_key)
        client.models.list()
        console.print("[green]✓[/green] OpenAI API key valid")
    except Exception as exc:
        console.print(f"[red]✗ OpenAI:[/red] {exc}")
        errors.append(str(exc))

    # 6. Reddit
    try:
        from config.settings import get_settings
        s = get_settings()
        if s.reddit_client_id:
            import praw
            reddit = praw.Reddit(
                client_id=s.reddit_client_id,
                client_secret=s.reddit_client_secret,
                username=s.reddit_username,
                password=s.reddit_password,
                user_agent=s.reddit_user_agent,
            )
            _ = reddit.user.me()
            console.print(f"[green]✓[/green] Reddit authenticated as u/{s.reddit_username}")
        else:
            console.print("[yellow]~[/yellow] Reddit: not configured (ENABLE_REDDIT=false)")
    except Exception as exc:
        console.print(f"[red]✗ Reddit:[/red] {exc}")
        errors.append(str(exc))

    # 7. Playwright
    try:
        import subprocess
        result = subprocess.run(
            ["python", "-m", "playwright", "install", "--dry-run"],
            capture_output=True, text=True
        )
        console.print("[green]✓[/green] Playwright available")
    except Exception as exc:
        console.print(f"[yellow]~[/yellow] Playwright check skipped: {exc}")

    console.print()
    if errors:
        console.print(
            f"[bold red]Setup complete with {len(errors)} error(s). "
            "Fix them before running 'python main.py run'.[/bold red]"
        )
        sys.exit(1)
    else:
        console.print("[bold green]All checks passed. You're ready to go![/bold green]")
        console.print("\nNext steps:")
        console.print("  1. python main.py scrape    — fill Pinterest image stock")
        console.print("  2. python main.py run       — start daily session")


@cli.command()
def status():
    """Show database statistics."""
    from database.sqlite_db import get_db
    stats = get_db().get_stats()

    console.print("\n[bold cyan]Fashion Bot — Status[/bold cyan]\n")
    table = Table(box=box.SIMPLE_HEAVY, show_header=False)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Pinterest images (total)", str(stats["pinterest_total"]))
    table.add_row("Pinterest images (unused)", str(stats["pinterest_unused"]))
    table.add_row("Products in cache", str(stats["products_cached"]))
    table.add_row("Posts (total)", str(stats["posts_total"]))
    for status_val, count in stats.get("posts_by_status", {}).items():
        table.add_row(f"  → {status_val}", str(count))
    console.print(table)


if __name__ == "__main__":
    cli()
