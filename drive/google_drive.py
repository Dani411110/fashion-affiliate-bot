"""Google Drive integration — upload, move, and organise post files."""

import json
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

from utils.logger import get_logger
from utils.retry import retry_on_network_error

logger = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_NAMES = {
    "queue": "Queue",
    "posted": "Posted",
    "rejected": "Rejected",
    "raw_pinterest": "Raw/Pinterest",
}


class DriveError(Exception):
    pass


class GoogleDriveClient:
    def __init__(self, service_account_json: str):
        self._service = self._build_service(service_account_json)
        logger.info("GoogleDriveClient initialised")

    @staticmethod
    def _build_service(service_account_json: str):
        try:
            try:
                info = json.loads(service_account_json)
                creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
            except json.JSONDecodeError:
                creds = Credentials.from_service_account_file(
                    service_account_json, scopes=_SCOPES
                )
            return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            raise DriveError(f"Drive auth failed: {exc}") from exc

    @retry_on_network_error
    def _create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        meta: Dict = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            meta["parents"] = [parent_id]
        result = self._service.files().create(body=meta, fields="id").execute()
        fid = result["id"]
        logger.debug("Created Drive folder '{}' id={}", name, fid)
        return fid

    @retry_on_network_error
    def _find_folder(self, name: str, parent_id: Optional[str] = None) -> Optional[str]:
        q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        results = self._service.files().list(q=q, fields="files(id,name)").execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        existing = self._find_folder(name, parent_id)
        if existing:
            return existing
        return self._create_folder(name, parent_id)

    def ensure_folder_structure(
        self,
        root_name: str = "FashionPosts",
        parent_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Create /FashionPosts/Queue, /Posted, /Rejected, /Raw/Pinterest if absent.

        Returns a dict mapping key→folder_id.
        """
        root_id = self.ensure_folder(root_name, parent_id)
        folder_ids = {"root": root_id}

        for key, sub_name in [
            ("queue", "Queue"),
            ("posted", "Posted"),
            ("rejected", "Rejected"),
        ]:
            folder_ids[key] = self.ensure_folder(sub_name, root_id)

        raw_id = self.ensure_folder("Raw", root_id)
        folder_ids["raw"] = raw_id
        folder_ids["raw_pinterest"] = self.ensure_folder("Pinterest", raw_id)

        logger.info("Drive folder structure ensured: {}", list(folder_ids.keys()))
        return folder_ids

    @retry_on_network_error
    def upload_file(
        self,
        local_path: Path,
        folder_id: str,
        filename: Optional[str] = None,
    ) -> str:
        """Upload a local file to Drive and return its shareable link."""
        local_path = Path(local_path)
        if not local_path.exists():
            raise DriveError(f"Local file not found: {local_path}")

        mime_type, _ = mimetypes.guess_type(str(local_path))
        mime_type = mime_type or "application/octet-stream"
        name = filename or local_path.name

        meta = {"name": name, "parents": [folder_id]}
        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)

        file = (
            self._service.files()
            .create(body=meta, media_body=media, fields="id")
            .execute()
        )
        file_id = file["id"]

        self._make_public(file_id)
        link = f"https://drive.google.com/file/d/{file_id}/view"
        logger.info("Uploaded {} → Drive {} ({})", local_path.name, file_id, link)
        return link

    @retry_on_network_error
    def _make_public(self, file_id: str):
        self._service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

    @retry_on_network_error
    def move_file(self, file_id: str, new_folder_id: str):
        file = (
            self._service.files()
            .get(fileId=file_id, fields="parents")
            .execute()
        )
        previous_parents = ",".join(file.get("parents", []))
        self._service.files().update(
            fileId=file_id,
            addParents=new_folder_id,
            removeParents=previous_parents,
            fields="id, parents",
        ).execute()
        logger.debug("Moved file {} to folder {}", file_id, new_folder_id)

    def upload_post_package(
        self,
        post_id: int,
        image_paths: List[Path],
        video_path: Optional[Path],
        caption_text: str,
        folder_id: str,
    ) -> Dict[str, str]:
        """Upload all post assets to a Drive folder. Returns dict of asset→link."""
        links: Dict[str, str] = {}

        for i, img_path in enumerate(image_paths):
            link = self.upload_file(
                img_path,
                folder_id,
                f"post_{post_id}_image_{i:02d}{img_path.suffix}",
            )
            links[f"image_{i}"] = link

        if video_path and Path(video_path).exists():
            link = self.upload_file(
                video_path,
                folder_id,
                f"post_{post_id}_video.mp4",
            )
            links["video"] = link

        caption_path = Path(f"/tmp/post_{post_id}_caption.txt")
        caption_path.write_text(caption_text, encoding="utf-8")
        link = self.upload_file(
            caption_path,
            folder_id,
            f"post_{post_id}_caption.txt",
        )
        links["caption"] = link
        caption_path.unlink(missing_ok=True)

        return links

    @retry_on_network_error
    def get_file_id_from_link(self, link: str) -> Optional[str]:
        """Extract Drive file_id from a shareable link."""
        if "/d/" in link:
            parts = link.split("/d/")
            if len(parts) > 1:
                return parts[1].split("/")[0]
        return None


_drive_instance: Optional[GoogleDriveClient] = None


def get_drive_client() -> GoogleDriveClient:
    global _drive_instance
    if _drive_instance is None:
        from config.settings import get_settings
        _drive_instance = GoogleDriveClient(get_settings().google_service_account_json)
    return _drive_instance
