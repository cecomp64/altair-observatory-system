"""Notification channels. Each takes a :class:`Message` and raises on failure;
the notifier records what was sent and retries on its next tick.

Secrets come from the environment (``*_env`` keys in altair.yaml), never from
the config file itself.
"""
from __future__ import annotations

import os
import smtplib
import subprocess
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import httpx


@dataclass
class Message:
    title: str
    body: str
    priority: str = "normal"          # normal / high (blocking issues)
    url: str | None = None            # the status page


class ChannelError(Exception):
    pass


def _env(cfg: dict, key: str) -> str:
    name = cfg.get(f"{key}_env")
    value = os.environ.get(name, "") if name else cfg.get(key, "")
    if not value:
        raise ChannelError(f"{cfg['type']}: set {name or key}")
    return value


def windows_toast(cfg: dict, msg: Message, *, run: Callable = subprocess.run) -> None:
    """A toast through PowerShell's WinRT bindings (no extra modules)."""
    if sys.platform != "win32" and not cfg.get("force"):
        return
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    launch = f" launch='{esc(msg.url)}' activationType='protocol'" if msg.url else ""
    xml = (f"<toast{launch}><visual><binding template='ToastGeneric'><text>{esc(msg.title)}</text>"
           f"<text>{esc(msg.body[:300])}</text></binding></visual></toast>")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{xml}');"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Altair').Show($t)"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=30, creationflags=flags)
    if result.returncode != 0:
        raise ChannelError(f"toast failed: {result.stderr!r}")


def pushover(cfg: dict, msg: Message, *, client: httpx.Client | None = None) -> None:
    data = {"token": _env(cfg, "token"), "user": _env(cfg, "user"), "title": msg.title, "message": msg.body[:1024],
            "priority": 1 if msg.priority == "high" else 0}
    if msg.url:
        data["url"] = msg.url
    _post(client, "https://api.pushover.net/1/messages.json", data=data)


def ntfy(cfg: dict, msg: Message, *, client: httpx.Client | None = None) -> None:
    server = cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = _env(cfg, "topic")
    headers = {"Title": msg.title.encode("ascii", "replace").decode(), "Priority": "high" if msg.priority == "high" else "default"}
    if cfg.get("token_env"):
        headers["Authorization"] = f"Bearer {_env(cfg, 'token')}"
    if msg.url:
        headers["Click"] = msg.url
    _post(client, f"{server}/{topic}", content=msg.body.encode(), headers=headers)


def email(cfg: dict, msg: Message, *, smtp_factory: Callable[..., Any] | None = None) -> None:
    """``smtp_url``: smtp[s]://user:password@host:port (from ``smtp_url_env``)."""
    url = urlparse(_env(cfg, "smtp_url"))
    to = cfg.get("to")
    if not to:
        raise ChannelError("email: set 'to'")
    mail = EmailMessage()
    mail["Subject"] = msg.title
    mail["From"] = cfg.get("from") or (unquote(url.username) if url.username and "@" in unquote(url.username) else "altair@localhost")
    mail["To"] = to
    mail.set_content(msg.body + (f"\n\nStatus page: {msg.url}" if msg.url else ""))
    factory = smtp_factory or (smtplib.SMTP_SSL if url.scheme == "smtps" else smtplib.SMTP)
    with factory(url.hostname, url.port or (465 if url.scheme == "smtps" else 587), timeout=30) as smtp:
        if url.scheme == "smtp" and cfg.get("starttls", True) and hasattr(smtp, "starttls"):
            smtp.starttls()
        if url.username:
            smtp.login(unquote(url.username), unquote(url.password or ""))
        smtp.send_message(mail)


def _post(client: httpx.Client | None, url: str, **kw) -> None:
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.post(url, **kw)
        if response.status_code >= 400:
            raise ChannelError(f"{url}: HTTP {response.status_code}")
    except httpx.HTTPError as exc:
        raise ChannelError(f"{url}: {exc}") from exc
    finally:
        if own:
            client.close()


CHANNELS: dict[str, Callable[..., None]] = {"windows_toast": windows_toast, "pushover": pushover, "ntfy": ntfy, "email": email,
                                            "hub": lambda cfg, msg: None}   # the Hub gets issues through the outbox
