"""
Daily session scheduler — builds, reviews, and queues 3 posts per day.

# PHASE 2 MIGRATION:
# This entire Scheduler class is replaced by a Telegram bot (python-telegram-bot).
# run_daily_session() → /run command sent to the bot by the operator.
# Category selection → inline keyboard sent per post.
# Approval → CLIApprovalInterface → TelegramApprovalInterface.
# post_scheduled() → APScheduler cron job running inside the bot process.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from categories.content_categories import CATEGORY_NAMES
from config.settings import get_settings
from core.approval_interface import CLIApprovalInterface
from core.post_builder import PostBuilder, PostPackage
from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)
console = Console()

POSTS_PER_DAY = 3


def _print_banner():
    today = datetime.now().strftime("%A, %B %d %Y")
    s = get_settings()
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]FASHION AFFILIATE BOT[/bold cyan]\n"
            f"[dim]{today}[/dim]\n\n"
            f"[bold]Posting slots:[/bold] "
            f"[yellow]{s.post_time_1}[/yellow] · "
            f"[yellow]{s.post_time_2}[/yellow] · "
            f"[yellow]{s.post_time_3}[/yellow]",
            border_style="cyan",
        )
    )
    console.print()


def _select_category(post_number: int) -> str:
    console.print(
        Panel(
            "\n".join(f"  [cyan]{k}[/cyan]. {v}" for k, v in CATEGORY_NAMES.items()),
            title=f"[bold]Post {post_number}/{POSTS_PER_DAY} — Select Category[/bold]",
            border_style="blue",
        )
    )
    while True:
        raw = console.input("[bold]Enter 1-4:[/bold] ").strip()
        try:
            num = int(raw)
            if num in CATEGORY_NAMES:
                return CATEGORY_NAMES[num]
        except ValueError:
            pass
        console.print("[red]Invalid choice. Enter 1, 2, 3, or 4.[/red]")


def _build_with_spinner(builder: PostBuilder, category: str) -> Optional[PostPackage]:
    result: Optional[PostPackage] = None
    error: Optional[Exception] = None

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("Building post…", total=None)
        try:
            result = builder.build_post(category)
        except Exception as exc:
            error = exc
        finally:
            progress.remove_task(task)

    if error:
        logger.exception("Post build failed for category '{}'", category)
        console.print(f"[bold red]Post build failed:[/bold red] {error}")
        return None
    return result


def _post_time_to_slot_label(slot_index: int) -> str:
    s = get_settings()
    times = [s.post_time_1, s.post_time_2, s.post_time_3]
    return times[slot_index] if slot_index < len(times) else "?"


def _publish_package(package: PostPackage):
    from config.settings import get_settings
    settings = get_settings()
    publishers = []

    if settings.enable_tiktok:
        from publishers.tiktok_publisher import TikTokPublisher
        publishers.append(
            TikTokPublisher(settings.tiktok_cookies_path, settings.tiktok_access_token)
        )
    if settings.enable_instagram:
        from publishers.instagram_publisher import InstagramPublisher
        publishers.append(
            InstagramPublisher(
                settings.instagram_access_token, settings.instagram_user_id
            )
        )
    if settings.enable_youtube:
        from publishers.youtube_publisher import YouTubePublisher
        publishers.append(
            YouTubePublisher(
                settings.youtube_client_secrets_json,
                token_path=Path(settings.youtube_token_path),
                token_json=settings.youtube_token_json or None,
                seconds_per_image=settings.video_seconds_per_image,
            )
        )

    if not publishers:
        console.print("[yellow]No platforms enabled — skipping publish.[/yellow]")
        return

    any_success = False
    for publisher in publishers:
        with Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]Publishing to {publisher.platform_name}…"),
            transient=True,
        ) as progress:
            task = progress.add_task("", total=None)
            result = publisher.publish(package)
            progress.remove_task(task)

        if result.success:
            any_success = True
            console.print(
                f"[green]✓ {publisher.platform_name.upper()}[/green]: {result.url or result.platform_post_id}"
            )
        else:
            console.print(
                f"[red]✗ {publisher.platform_name.upper()}[/red]: {result.error}"
            )

    if any_success:
        db = get_db()
        db.mark_post_status(package.post_id, "posted")


class Scheduler:
    def __init__(self):
        self._builder = PostBuilder()
        self._approval = CLIApprovalInterface()
        self._db = get_db()

    def run_daily_session(self):
        """Interactive daily workflow: build → review → queue 3 posts."""
        _print_banner()

        packages: List[PostPackage] = []

        for i in range(1, POSTS_PER_DAY + 1):
            console.rule(f"[bold cyan]POST {i} OF {POSTS_PER_DAY}[/bold cyan]")
            console.print(
                f"[dim]Scheduled for slot: {_post_time_to_slot_label(i - 1)}[/dim]\n"
            )

            category = _select_category(i)
            console.print(f"\n[bold]Building:[/bold] [cyan]{category}[/cyan]…\n")

            package = _build_with_spinner(self._builder, category)
            if not package:
                console.print(
                    f"[yellow]Skipping post {i} due to build failure.[/yellow]"
                )
                continue

            approved = self._approval.review_post(package)
            if approved:
                packages.append(package)

        # Summary
        console.print()
        console.print(
            Panel(
                f"Session complete.\n"
                f"[green]{len(packages)}[/green] post(s) approved and queued.\n"
                f"[red]{POSTS_PER_DAY - len(packages)}[/red] post(s) skipped/rejected.",
                title="[bold]Summary[/bold]",
                border_style="green",
            )
        )

        if not packages:
            console.print("[yellow]No posts to publish.[/yellow]")
            return

        # Ask post now vs schedule
        console.print("\n[bold]Ready to publish?[/bold]")
        console.print(
            "  [green]n[/green] — post NOW\n"
            "  [blue]s[/blue] — save to queue (publish later with: python main.py post-queue)\n"
            "  [red]q[/red] — quit without publishing"
        )
        choice = console.input("[n/s/q]> ").strip().lower()

        if choice == "n":
            for pkg in packages:
                console.rule(f"[cyan]Publishing post {pkg.post_id}[/cyan]")
                _publish_package(pkg)
        elif choice == "s":
            console.print(
                f"[blue]{len(packages)} post(s) saved to queue.[/blue] "
                "Run [bold]python main.py post-queue[/bold] to publish."
            )
        else:
            console.print("[dim]Exiting without publishing.[/dim]")

    def post_scheduled(self):
        """Publish all approved posts that are overdue for their scheduled slot."""
        now = datetime.now()
        approved_posts = self._db.get_posts_by_status("approved")
        if not approved_posts:
            logger.info("post_scheduled: no approved posts in queue")
            return

        settings = get_settings()
        slot_times = [settings.post_time_1, settings.post_time_2, settings.post_time_3]

        for post in approved_posts:
            from core.post_builder import package_from_db_record

            record = self._db.get_post(post["id"])
            if not record:
                continue

            pkg = package_from_db_record(record)
            logger.info("Publishing queued post {}", pkg.post_id)
            _publish_package(pkg)
