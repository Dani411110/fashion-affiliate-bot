"""CLI entry point for the Fashion Affiliate Content Automation Bot."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


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
