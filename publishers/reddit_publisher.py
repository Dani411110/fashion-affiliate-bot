"""Reddit publisher — image gallery post with affiliate link comment."""

import time
from pathlib import Path
from typing import Any, List

import praw
from praw.exceptions import RedditAPIException

from publishers.base_publisher import BasePublisher, PublishError, PublishResult
from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)


class RedditPublisher(BasePublisher):
    platform_name = "reddit"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        user_agent: str,
        subreddit: str,
    ):
        self._subreddit_name = subreddit
        self._reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent=user_agent,
        )
        logger.info("RedditPublisher initialised for r/{}", subreddit)

    @retry_on_network_error
    def _submit_gallery(
        self,
        subreddit_name: str,
        title: str,
        image_paths: List[Path],
    ) -> Any:
        subreddit = self._reddit.subreddit(subreddit_name)
        images = []
        for path in image_paths:
            p = Path(path)
            if not p.exists():
                continue
            images.append({"image_path": str(p), "caption": ""})
        if not images:
            raise PublishError("No valid images for Reddit gallery")
        submission = subreddit.submit_gallery(title=title, images=images)
        return submission

    @retry_on_network_error
    def _add_affiliate_comment(self, submission: Any, comment_body: str):
        comment = submission.reply(comment_body)
        try:
            comment.mod.distinguish(sticky=True)
        except Exception:
            pass  # May not have mod permissions
        return comment

    def publish(self, post_package: Any) -> PublishResult:
        try:
            captions = post_package.captions.get("reddit", {})
            title = captions.get("title", "Fashion Finds")[:300]
            caption_body = post_package.formatted_captions.get("reddit", "")

            all_images: List[Path] = []
            if post_package.pinterest_image_path and Path(post_package.pinterest_image_path).exists():
                all_images.append(Path(post_package.pinterest_image_path))
            for img_path in (post_package.product_images or []):
                p = Path(img_path)
                if p.exists():
                    all_images.append(p)

            if not all_images:
                raise PublishError("No images available for Reddit post")

            # Reddit gallery max 20 images
            all_images = all_images[:20]

            submission = self._submit_gallery(self._subreddit_name, title, all_images)
            sub_id = submission.id
            sub_url = f"https://www.reddit.com{submission.permalink}"

            if caption_body:
                time.sleep(2)
                self._add_affiliate_comment(submission, caption_body)

            result = PublishResult(
                success=True,
                platform_post_id=sub_id,
                url=sub_url,
            )

        except RedditAPIException as exc:
            result = PublishResult(success=False, error=f"Reddit API error: {exc}")
        except PublishError as exc:
            result = PublishResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("Unexpected Reddit publish error")
            result = PublishResult(success=False, error=str(exc))

        self.update_db_status(post_package.post_id, result)
        return result
