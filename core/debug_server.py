"""Tiny read-only debug dashboard for Railway.

The dashboard intentionally has no mutation endpoints. It is meant to answer:
is the bot alive, what DB does it see, and what did it log recently?
"""

from __future__ import annotations

import html
import json
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
    platform_html = "\n".join(
        f"<span class='pill {'on' if enabled else 'off'}'>{name}: {'ON' if enabled else 'OFF'}</span>"
        for name, enabled in payload["platforms"].items()
    )
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
    <section class="panel"><h2>Last Event</h2><pre>{html.escape(payload["last_log_event"])}</pre></section>
    <section class="panel"><h2>Status JSON</h2><pre>{status_json}</pre></section>
    <section class="panel"><h2>Recent Logs</h2><pre>{logs_html}</pre></section>
  </main>
</body>
</html>"""


def start_debug_server(settings: Settings) -> ThreadingHTTPServer | None:
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
            if parsed.path != "/health" and not _authorized(query):
                _json_response(self, {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return

            if parsed.path == "/health":
                _json_response(self, {"ok": True, "status": "online"})
            elif parsed.path == "/api/status":
                _json_response(self, _status_payload(settings))
            elif parsed.path == "/api/logs":
                log_file = _latest_log_file()
                _json_response(self, {"log_file": str(log_file) if log_file else "", "lines": _tail_file(log_file, 120) if log_file else []})
            elif parsed.path == "/":
                _html_response(self, _dashboard_html(settings, token_query))
            else:
                _json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)

    httpd = ThreadingHTTPServer(("0.0.0.0", port), DebugHandler)
    thread = threading.Thread(target=httpd.serve_forever, name="debug-ui", daemon=True)
    thread.start()
    logger.info("Debug UI listening on port {}", port)
    return httpd
