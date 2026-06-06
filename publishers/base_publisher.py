"""Abstract base class for all platform publishers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


class PublishError(Exception):
    pass


@dataclass
class PublishResult:
    success: bool
    platform_post_id: str = ""
    url: str = ""
    error: str = ""


class BasePublisher(ABC):
    platform_name: str = "unknown"

    def update_db_status(self, post_id: int, result: PublishResult):
        db = get_db()
        status = "posted" if result.success else "failed"
        db.upsert_platform_status(
            post_id=post_id,
            platform=self.platform_name,
            status=status,
            platform_post_id=result.platform_post_id,
            error_message=result.error,
        )
        if result.success:
            logger.info(
                "[{}] Post {} published: {} — {}",
                self.platform_name,
                post_id,
                result.platform_post_id,
                result.url,
            )
        else:
            logger.error(
                "[{}] Post {} FAILED: {}",
                self.platform_name,
                post_id,
                result.error,
            )

    @abstractmethod
    def publish(self, post_package: Any) -> PublishResult:
        """Publish *post_package* to the platform. Return PublishResult."""
        ...
