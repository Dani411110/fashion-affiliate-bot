"""Tiny read-only debug dashboard for Railway.

The dashboard intentionally has no mutation endpoints. It is meant to answer:
is the bot alive, what DB does it see, and what did it log recently?
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from config.settings import Settings
from database.sqlite_db import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

_STARTED_AT = datetime.now(timezone.utc)
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"ntn_[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r'("' + "private" + r'_key"\s*:\s*")[^"]+(")'),
]


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _text_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


_PUBLIC_PATHS = {
    "/health",
    "/privacy",
    "/terms",
    "/tiktok/demo",
    "/tiktok/callback",
    "/tiktok/callback/",
    "/tiktok/callback/tiktokjfxbs3iqzCMcq2dxj1SIJ0lILoUIXDnq.txt",
    "/tiktokjfxbs3iqzCMcq2dxj1SIJ0lILoUIXDnq.txt",
    "/youtube/callback",
    "/youtube/callback/",
}

_TIKTOK_SITE_VERIFICATION = "tiktok-developers-site-verification=jfxbs3iqzCMcq2dxj1SIJ0lILoUIXDnq"


def _sanitize(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if ("private" + "_key") in pattern.pattern:
            text = pattern.sub(r'\1***\2', text)
        else:
            text = pattern.sub("***", text)
    return text


def _tail_file(path: Path, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [_sanitize(line) for line in lines[-limit:]]


def _latest_log_file() -> Path | None:
    log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
    files = sorted(log_dir.glob("fashion_bot_*.log"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _last_log_event(lines: list[str]) -> str:
    if not lines:
        return "No log file yet"
    return lines[-1]


def _safe_db_stats() -> dict[str, Any]:
    try:
        return get_db().get_stats()
    except Exception as exc:
        return {"error": str(exc)}


def _platform_readiness(settings: Settings) -> dict[str, Any]:
    cookies_path = Path(settings.tiktok_cookies_path)
    youtube_token = Path(settings.youtube_token_path)
    youtube_token_ok = youtube_token.exists() or bool(settings.youtube_token_json)
    drive_ready = all(
        [
            settings.drive_folder_queue_id,
            settings.drive_folder_posted_id,
            settings.drive_folder_rejected_id,
        ]
    )
    platforms = {
        "reddit": {
            "enabled": settings.enable_reddit,
            "configured": all(
                [
                    settings.reddit_client_id,
                    settings.reddit_client_secret,
                    settings.reddit_username,
                    settings.reddit_password,
                    settings.reddit_subreddit,
                ]
            ),
            "next_action": "Waiting for Reddit API credentials/approval",
        },
        "instagram": {
            "enabled": settings.enable_instagram,
            "configured": bool(settings.instagram_access_token and settings.instagram_user_id and drive_ready),
            "next_action": "Waiting for Meta/Instagram access token, user id, and Drive folder IDs",
        },
        "tiktok": {
            "enabled": settings.enable_tiktok,
            "configured": bool(settings.tiktok_access_token or cookies_path.exists()),
            "oauth_ready": bool(settings.tiktok_client_key and settings.tiktok_client_secret and settings.tiktok_redirect_uri),
            "next_action": "Waiting for TikTok review, then OAuth code exchange for TIKTOK_ACCESS_TOKEN",
        },
        "youtube": {
            "enabled": settings.enable_youtube,
            "configured": bool(settings.youtube_client_secrets_json and youtube_token_ok),
            "client_secrets": bool(settings.youtube_client_secrets_json),
            "oauth_token": youtube_token_ok,
            "next_action": "Run local OAuth and store data/youtube_token.json, then enable YouTube",
        },
    }
    blockers = [
        f"{name}: {data['next_action']}"
        for name, data in platforms.items()
        if not data.get("configured")
    ]
    return {
        "ready_to_publish_anywhere": any(
            data["enabled"] and data["configured"] for data in platforms.values()
        ),
        "drive_ready": drive_ready,
        "platforms": platforms,
        "blockers": blockers,
    }


def _status_payload(settings: Settings) -> dict[str, Any]:
    log_file = _latest_log_file()
    log_lines = _tail_file(log_file, 40) if log_file else []
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
    now = datetime.now(timezone.utc)
    return {
        "status": "online",
        "started_at": _STARTED_AT.isoformat(),
        "uptime_seconds": int((now - _STARTED_AT).total_seconds()),
        "now": now.isoformat(),
        "railway_volume": volume or "not detected",
        "sqlite_path": str(settings.sqlite_path),
        "debug_ui_auth": "enabled" if os.getenv("DEBUG_UI_TOKEN") else "disabled",
        "platforms": {
            "reddit": settings.enable_reddit,
            "instagram": settings.enable_instagram,
            "tiktok": settings.enable_tiktok,
            "youtube": settings.enable_youtube,
        },
        "schedule": [settings.post_time_1, settings.post_time_2, settings.post_time_3],
        "db": _safe_db_stats(),
        "readiness": _platform_readiness(settings),
        "log_file": str(log_file) if log_file else "",
        "last_log_event": _last_log_event(log_lines),
    }


def _authorized(query: dict[str, list[str]]) -> bool:
    token = os.getenv("DEBUG_UI_TOKEN", "").strip()
    if not token:
        return True
    supplied = (query.get("token") or [""])[0]
    return supplied == token


def _dashboard_html(settings: Settings, token_query: str) -> str:
    payload = _status_payload(settings)
    log_file = _latest_log_file()
    logs = _tail_file(log_file, 100) if log_file else []
    stats = payload.get("db", {})
    cards = [
        ("Status", payload["status"]),
        ("Uptime", f"{payload['uptime_seconds']}s"),
        ("Products", stats.get("products_cached", "?")),
        ("Pinterest unused", stats.get("pinterest_unused", "?")),
        ("Posts", stats.get("posts_total", "?")),
        ("Volume", payload["railway_volume"]),
    ]
    card_html = "\n".join(
        f"<div class='card'><div class='label'>{html.escape(str(label))}</div>"
        f"<div class='value'>{html.escape(str(value))}</div></div>"
        for label, value in cards
    )
    readiness = payload["readiness"]
    platform_html = "\n".join(
        f"<div class='row'><span class='pill {'on' if data['enabled'] else 'off'}'>"
        f"{name}: {'ON' if data['enabled'] else 'OFF'}</span>"
        f"<span class='muted'>configured={'yes' if data['configured'] else 'no'}; "
        f"{html.escape(str(data['next_action']))}</span></div>"
        for name, data in readiness["platforms"].items()
    )
    blocker_html = "\n".join(f"<li>{html.escape(item)}</li>" for item in readiness["blockers"])
    logs_html = html.escape("\n".join(logs))
    status_json = html.escape(json.dumps(payload, ensure_ascii=False, indent=2))
    refresh_url = f"/{token_query}"
    api_status = f"/api/status{token_query}"
    api_logs = f"/api/logs{token_query}"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>Fashion Bot Debug</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ margin:0; font-family: Inter, ui-sans-serif, system-ui, Arial, sans-serif; background:#101114; color:#f2f2f3; }}
    header {{ padding:20px 28px; border-bottom:1px solid #2b2d35; display:flex; justify-content:space-between; align-items:center; }}
    h1 {{ font-size:22px; margin:0; }}
    main {{ padding:24px 28px; max-width:1200px; margin:0 auto; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:18px; }}
    .card {{ background:#191b22; border:1px solid #2b2d35; border-radius:8px; padding:14px; }}
    .label {{ color:#a5a9b8; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    .value {{ font-size:20px; margin-top:8px; overflow-wrap:anywhere; }}
    .panel {{ background:#191b22; border:1px solid #2b2d35; border-radius:8px; padding:16px; margin-top:14px; }}
    .pill {{ display:inline-block; margin:0 8px 8px 0; padding:6px 10px; border-radius:999px; font-size:13px; }}
    .row {{ margin:8px 0; }}
    .on {{ background:#12351f; color:#8df0a5; }}
    .off {{ background:#3a1720; color:#ff9daf; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; margin:0; font:12px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; }}
    a {{ color:#9f8cff; text-decoration:none; }}
    .muted {{ color:#a5a9b8; font-size:13px; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Fashion Bot Debug</h1>
      <div class="muted">Auto-refresh every 15s. Read-only.</div>
    </div>
    <nav><a href="{refresh_url}">Refresh</a> · <a href="{api_status}">API status</a> · <a href="{api_logs}">API logs</a></nav>
  </header>
  <main>
    <section class="grid">{card_html}</section>
    <section class="panel"><h2>Platforms</h2>{platform_html}</section>
    <section class="panel"><h2>Blockers</h2><ul>{blocker_html}</ul></section>
    <section class="panel"><h2>Last Event</h2><pre>{html.escape(payload["last_log_event"])}</pre></section>
    <section class="panel"><h2>Status JSON</h2><pre>{status_json}</pre></section>
    <section class="panel"><h2>Recent Logs</h2><pre>{logs_html}</pre></section>
  </main>
</body>
</html>"""


def _legal_page_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Fashion Bot</title>
  <style>
    body {{ margin:0; font-family: Arial, sans-serif; background:#fafafa; color:#171717; }}
    main {{ max-width:760px; margin:0 auto; padding:48px 24px; line-height:1.65; }}
    h1 {{ font-size:30px; margin:0 0 20px; }}
    p {{ margin:0 0 14px; }}
    a {{ color:#4f46e5; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    {body}
  </main>
</body>
</html>"""


def _privacy_html() -> str:
    body = """
    <p>Fashion Bot is an internal operator tool for preparing, reviewing, and publishing fashion affiliate content to connected social accounts.</p>
    <p>The service may process account identifiers, approved post captions, image URLs, and publishing status for the sole purpose of operating the content workflow.</p>
    <p>We do not sell personal data. Access tokens are stored as environment secrets and are used only to publish content that the operator manually approves.</p>
    <p>To request deletion of app-related data, contact the app owner through the connected developer account.</p>
    """
    return _legal_page_html("Privacy Policy", body)


def _terms_html() -> str:
    body = """
    <p>Fashion Bot is provided as an internal automation tool for managing fashion affiliate content workflows.</p>
    <p>The operator is responsible for reviewing all generated content, complying with platform rules, and ensuring that affiliate links and disclosures are accurate.</p>
    <p>The tool does not guarantee successful publication to third-party platforms and may be limited by API availability, account permissions, or platform review decisions.</p>
    <p>By using this service, the operator agrees to publish only approved content and to maintain valid credentials for connected accounts.</p>
    """
    return _legal_page_html("Terms of Service", body)


def _tiktok_callback_html(query: dict[str, list[str]]) -> str:
    code = html.escape((query.get("code") or [""])[0])
    error = html.escape((query.get("error") or [""])[0])
    if error:
        body = f"<p>TikTok authorization returned an error: <strong>{error}</strong></p>"
    elif code:
        body = (
            "<p>TikTok authorization succeeded. Copy this authorization code for the local token helper:</p>"
            f"<p><code>{code}</code></p>"
        )
    else:
        body = "<p>TikTok callback endpoint is active.</p>"
    return _legal_page_html("TikTok Callback", body)


def _youtube_callback_html(query: dict[str, list[str]], settings: Settings) -> str:
    code = (query.get("code") or [""])[0]
    error = html.escape((query.get("error") or [""])[0])

    if error:
        body = f"<p>YouTube authorization error: <strong>{error}</strong></p><p>Go back and try again.</p>"
        return _legal_page_html("YouTube OAuth Callback", body)

    if not code:
        body = "<p>YouTube OAuth callback endpoint is active. No code received yet.</p>"
        return _legal_page_html("YouTube OAuth Callback", body)

    try:
        from google_auth_oauthlib.flow import Flow  # type: ignore

        secrets_src = settings.youtube_client_secrets_json or ""
        secrets_path = secrets_src
        if secrets_src and not Path(secrets_src).exists():
            tmp = Path("data/youtube_client_secrets.json")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(secrets_src, encoding="utf-8")
            secrets_path = str(tmp)

        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if railway_domain:
            redirect_uri = f"https://{railway_domain}/youtube/callback"
        else:
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
        logger.info("YouTube OAuth token saved via Railway callback")
        body = (
            "<p><strong>✅ YouTube connected successfully!</strong></p>"
            "<p>Token saved. Now set <code>ENABLE_YOUTUBE=true</code> in Railway Variables and redeploy.</p>"
        )
    except Exception as exc:
        logger.exception("YouTube callback token exchange failed")
        body = f"<p>❌ Token exchange failed: <code>{html.escape(str(exc))}</code></p><p>Try generating a new auth URL.</p>"

    return _legal_page_html("YouTube OAuth Callback", body)


def _tiktok_demo_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fashion Bot TikTok Demo</title>
  <style>
    body { margin:0; font-family: Arial, sans-serif; background:#0f1014; color:#f7f7f8; }
    main { max-width:920px; margin:0 auto; padding:42px 22px; }
    h1 { margin:0 0 10px; font-size:32px; }
    p { color:#c8cad3; line-height:1.55; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:22px; }
    .card { border:1px solid #2b2d35; border-radius:8px; background:#191b22; padding:16px; }
    .step { color:#9f8cff; font-size:13px; text-transform:uppercase; letter-spacing:.04em; }
    .title { margin-top:8px; font-size:19px; font-weight:700; }
    .button { display:inline-block; margin-top:18px; padding:12px 16px; border-radius:8px; background:#fe2c55; color:white; text-decoration:none; font-weight:700; }
    code { color:#8df0a5; }
  </style>
</head>
<body>
  <main>
    <h1>Fashion Bot TikTok Integration Demo</h1>
    <p>
      Fashion Bot prepares fashion affiliate drafts, sends them to the operator for Telegram approval,
      and publishes only approved posts to connected social accounts.
    </p>
    <a class="button" href="/tiktok/callback">TikTok callback endpoint active</a>
    <section class="grid">
      <div class="card"><div class="step">Step 1</div><div class="title">Connect TikTok</div><p>Login Kit identifies the authorized TikTok account before publishing.</p></div>
      <div class="card"><div class="step">Step 2</div><div class="title">Build Draft</div><p>The bot prepares images, products, captions, and affiliate links for review.</p></div>
      <div class="card"><div class="step">Step 3</div><div class="title">Manual Approval</div><p>The operator receives a Telegram preview and chooses approve, reject, or regenerate.</p></div>
      <div class="card"><div class="step">Step 4</div><div class="title">Publish</div><p>Content Posting API uploads only approved content to the connected TikTok profile.</p></div>
    </section>
    <p>Public endpoints used for review: <code>/privacy</code>, <code>/terms</code>, and <code>/tiktok/callback</code>.</p>
  </main>
</body>
</html>"""


def _bootstrap_youtube_token(settings: Settings) -> None:
    """Write YOUTUBE_TOKEN_JSON env var to disk at startup if token file is missing."""
    if not settings.youtube_token_json:
        return
    token_path = Path(settings.youtube_token_path)
    if not token_path.exists():
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(settings.youtube_token_json, encoding="utf-8")
        logger.info("YouTube token bootstrapped from env var to {}", token_path)


def start_debug_server(settings: Settings) -> ThreadingHTTPServer | None:
    _bootstrap_youtube_token(settings)

    if os.getenv("ENABLE_DEBUG_UI", "true").strip().lower() in {"0", "false", "no"}:
        logger.info("Debug UI disabled")
        return None

    raw_port = os.getenv("PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError:
        port = 8080

    class DebugHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args):
            logger.debug("Debug UI: " + fmt, *args)

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            token_query = f"?token={html.escape(query.get('token', [''])[0])}" if query.get("token") else ""
            if (not parsed.path.startswith("/images/")
                    and parsed.path not in _PUBLIC_PATHS
                    and not _authorized(query)):
                _json_response(self, {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return

            if parsed.path == "/health":
                _json_response(self, {"ok": True, "status": "online"})
            elif parsed.path == "/api/status":
                _json_response(self, _status_payload(settings))
            elif parsed.path == "/api/readiness":
                _json_response(self, _platform_readiness(settings))
            elif parsed.path == "/api/logs":
                log_file = _latest_log_file()
                _json_response(self, {"log_file": str(log_file) if log_file else "", "lines": _tail_file(log_file, 120) if log_file else []})
            elif parsed.path == "/":
                _html_response(self, _dashboard_html(settings, token_query))
            elif parsed.path == "/privacy":
                _html_response(self, _privacy_html())
            elif parsed.path == "/terms":
                _html_response(self, _terms_html())
            elif parsed.path == "/tiktok/demo":
                _html_response(self, _tiktok_demo_html())
            elif parsed.path in {"/tiktok/callback", "/tiktok/callback/"}:
                _html_response(self, _tiktok_callback_html(query))
            elif parsed.path in {"/youtube/callback", "/youtube/callback/"}:
                _html_response(self, _youtube_callback_html(query, settings))
            elif parsed.path in {
                "/tiktokjfxbs3iqzCMcq2dxj1SIJ0lILoUIXDnq.txt",
                "/tiktok/callback/tiktokjfxbs3iqzCMcq2dxj1SIJ0lILoUIXDnq.txt",
            }:
                _text_response(self, _TIKTOK_SITE_VERIFICATION)
            elif parsed.path.startswith("/images/"):
                filename = parsed.path[len("/images/"):]
                if ".." in filename or filename.startswith("/"):
                    _json_response(self, {"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                    return
                file_path = Path("/data/public_images") / filename
                if not file_path.exists() or not file_path.is_file():
                    _json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                mime_type, _ = mimetypes.guess_type(str(file_path))
                content = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                _json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)

    httpd = ThreadingHTTPServer(("0.0.0.0", port), DebugHandler)
    thread = threading.Thread(target=httpd.serve_forever, name="debug-ui", daemon=True)
    thread.start()
    logger.info("Debug UI listening on port {}", port)
    return httpd
