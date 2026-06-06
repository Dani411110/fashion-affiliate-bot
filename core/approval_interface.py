"""
CLI approval interface for reviewing carousel posts before publishing.

# PHASE 2 MIGRATION:
# Replace CLIApprovalInterface with TelegramApprovalInterface.
# - review_post() sends the Pinterest image as a Telegram photo to the operator.
# - Products + captions sent as formatted message.
# - Inline keyboard: [✅ Approve] [❌ Reject] [🔄 Regenerate captions]
# - Returns True/False via asyncio.Queue awaited by the bot handler.
"""

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from captions.caption_generator import get_caption_generator
from database.sqlite_db import get_db
from drive.google_drive import get_drive_client
from utils.logger import get_logger

logger = get_logger(__name__)
console = Console()


class CLIApprovalInterface:
    def __init__(self):
        self._db = get_db()

    def _display_post(self, post_package: Any):
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]POST REVIEW[/bold cyan] — ID: [yellow]{post_package.post_id}[/yellow]",
                border_style="cyan",
            )
        )

        console.print(f"\n[bold]Category:[/bold] {post_package.category}")
        console.print(f"[bold]Pinterest image:[/bold] {post_package.pinterest_image_path}")
        console.print(
            f"[bold]Carousel images:[/bold] {len(post_package.all_images)} total "
            f"(1 inspiration + {len(post_package.product_images)} products)"
        )
        if post_package.public_image_urls:
            console.print(
                f"[bold]Public URLs ready:[/bold] {len(post_package.public_image_urls)} "
                "(uploaded to Drive ✓)"
            )

        # Products table
        table = Table(
            title="Products in this carousel",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("#", width=4)
        table.add_column("Name", min_width=22)
        table.add_column("Price", width=8)
        table.add_column("Category", width=14)
        table.add_column("Score", width=6)
        table.add_column("Mulebuy Link", min_width=30)

        for i, p in enumerate(post_package.products or [], start=1):
            table.add_row(
                str(i),
                p.get("name", "—"),
                f"${p.get('price', 0):.2f}",
                p.get("category", "—"),
                str(p.get("popularity_score", 0)),
                p.get("mulebuy_link", "—"),
            )
        console.print(table)

        # Captions per platform
        console.print("\n[bold underline]Captions[/bold underline]")
        for platform, cap_data in (post_package.captions or {}).items():
            hashtags_preview = ", ".join(cap_data.get("hashtags", [])[:8])
            if len(cap_data.get("hashtags", [])) > 8:
                hashtags_preview += f"… (+{len(cap_data['hashtags']) - 8} more)"
            console.print(
                Panel(
                    f"[bold]Title:[/bold]   {cap_data.get('title', '')}\n\n"
                    f"[bold]Caption:[/bold] {cap_data.get('caption', '')}\n\n"
                    f"[bold]Tags:[/bold]    {hashtags_preview}",
                    title=f"[cyan]{platform.upper()}[/cyan]",
                    border_style="dim",
                )
            )

    def _prompt(self) -> str:
        while True:
            console.print(
                "\n[bold green]y[/bold green] approve  "
                "[bold red]n[/bold red] reject  "
                "[bold yellow]r[/bold yellow] regenerate captions"
            )
            answer = console.input("[y/n/r]> ").strip().lower()
            if answer in ("y", "n", "r"):
                return answer
            console.print("[red]Enter y, n, or r.[/red]")

    def review_post(self, post_package: Any) -> bool:
        """Display post and prompt for approval. Returns True if approved.

        Loops on 'r' (regenerate) until user approves or rejects.
        """
        while True:
            self._display_post(post_package)
            choice = self._prompt()

            if choice == "y":
                self._db.mark_post_status(post_package.post_id, "approved")
                post_package.status = "approved"
                logger.info("Post {} approved", post_package.post_id)
                console.print("\n[bold green]✓ Post approved.[/bold green]")
                return True

            elif choice == "n":
                self._db.mark_post_status(post_package.post_id, "rejected")
                post_package.status = "rejected"
                logger.info("Post {} rejected", post_package.post_id)

                # Move Drive files to /Rejected/ folder
                from config.settings import get_settings
                rejected_folder = get_settings().drive_folder_rejected_id
                if rejected_folder and post_package.public_image_urls:
                    drive = get_drive_client()
                    for url in post_package.public_image_urls:
                        try:
                            file_id = drive.get_file_id_from_link(url)
                            if file_id:
                                drive.move_file(file_id, rejected_folder)
                        except Exception:
                            logger.warning("Could not move Drive file to /Rejected/")

                console.print("\n[bold red]✗ Post rejected.[/bold red]")
                return False

            elif choice == "r":
                logger.info("Regenerating captions for post {}", post_package.post_id)
                console.print("[yellow]Regenerating captions…[/yellow]")
                try:
                    cap_gen = get_caption_generator()
                    new_captions = cap_gen.generate_all_platforms(
                        Path(post_package.pinterest_image_path),
                        post_package.products,
                        post_package.category,
                    )
                    new_formatted = {
                        platform: cap_gen.format_for_platform(cap_data, post_package.products, platform)
                        for platform, cap_data in new_captions.items()
                    }
                    post_package.captions = new_captions
                    post_package.formatted_captions = new_formatted
                    self._db.update_post_captions(
                        post_package.post_id,
                        new_formatted.get("reddit", ""),
                        " ".join(
                            f"#{h}" for h in new_captions.get("reddit", {}).get("hashtags", [])
                        ),
                    )
                    console.print("[green]Captions regenerated.[/green]")
                except Exception:
                    logger.exception("Caption regeneration failed")
                    console.print("[red]Regeneration failed — showing previous captions.[/red]")
