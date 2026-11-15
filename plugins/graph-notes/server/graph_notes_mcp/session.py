"""The Cloudflare Access session for the graph app, from ``cloudflared``.

``cloudflared access login <app>`` opens the browser once and saves the session under
``~/.cloudflared``; ``cloudflared access token -app=<app>`` then prints it with no
browser, for as long as it lasts (24 hours).

The session is a secret. It is read from that command's captured output, kept only in
this process's memory and handed to the HTTP client, which sends it as a request
header. It is never printed, logged, written to a file, put on a command line or in an
error. ``login`` prints the session when it succeeds, so its output is discarded
unread; a stdio MCP server's own stdout carries protocol messages only, so no child
ever inherits it.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("graph_notes_mcp")

TOKEN_SECONDS = 30    # `access token` asks Cloudflare about the app, then reads a file
LOGIN_SECONDS = 300   # a person opening an email and typing its code
EXPIRY_LEEWAY = 30
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

INSTALL = (
    "The graph-notes MCP server signs in through `cloudflared`, which is not installed (or not on PATH). "
    "Install it (macOS: `brew install cloudflared`; other systems: "
    "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), then try again."
)


class SignInError(Exception):
    """No usable session; the message tells the person what to do and holds no secret."""


def expired(token: str, now: float, leeway: float = 0) -> bool:
    """Whether the session's own ``exp`` has passed, or is within ``leeway`` seconds. Unreadable means "ask the app"."""
    try:
        payload = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
        return now > float(exp) - leeway
    except (ValueError, KeyError, TypeError, IndexError):
        return False


@dataclass
class Session:
    """The session for the Access application at ``app_url``.

    ``run`` is ``subprocess.run`` (tests pass a stand-in for ``cloudflared``); ``store`` is
    where ``cloudflared`` saves its sessions.
    """

    app_url: str
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    store: Path = field(default_factory=lambda: Path.home() / ".cloudflared")
    clock: Callable[[], float] = time.time
    _token: str | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def token(self) -> str:
        """The session, signing in through the browser first when none is saved."""
        with self._lock:
            if self._token is None or expired(self._token, self.clock(), EXPIRY_LEEWAY):
                self._token = self._saved() or self._signed_in()
            return self._token

    def renew(self, refused: str) -> str:
        """A session to use after the app refused ``refused``: one another call has
        already fetched, one saved since, or a fresh sign-in. ``cloudflared login`` hands
        back a saved session that has not expired, so the refused one is removed first."""
        with self._lock:
            if self._token in (None, refused) or expired(self._token, self.clock(), EXPIRY_LEEWAY):
                saved = self._saved()
                if saved == refused:
                    self._forget(refused)
                    saved = None
                fresh = saved or self._signed_in()
                if fresh == refused:
                    raise SignInError(
                        f"{self.app_url} refused the session cloudflared has saved, and signing in again handed "
                        "back that same session. Wait a minute if it is about to expire; otherwise remove "
                        f"cloudflared's saved token for this app from {self.store} and try again."
                    )
                self._token = fresh
            return self._token

    def redacted(self, text: str) -> str:
        """``text`` with the session cut out of it, should anything ever have put it there."""
        return text.replace(self._token, "[session]") if self._token else text

    def _cloudflared(self, *args: str, stdout: int, seconds: float) -> subprocess.CompletedProcess | None:
        """Runs ``cloudflared access ...`` with nothing inherited; None when it ran out of time."""
        try:
            return self.run(["cloudflared", "access", *args], stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.DEVNULL, text=True, timeout=seconds)
        except FileNotFoundError:
            raise SignInError(INSTALL) from None
        except subprocess.TimeoutExpired:
            return None  # the exception holds the captured output: dropped here, never raised on

    def _forget(self, refused: str) -> None:
        """Removes ``cloudflared``'s saved copy of ``refused``, found by its content."""
        try:
            files = [path for path in self.store.iterdir() if path.is_file()]
        except OSError:
            return
        for path in files:
            try:
                if path.read_text().strip() == refused:
                    path.unlink()
            except (OSError, UnicodeDecodeError):
                continue

    def _saved(self) -> str | None:
        """The session ``cloudflared`` has on disk, if it has one that has not expired."""
        done = self._cloudflared("token", f"-app={self.app_url}", stdout=subprocess.PIPE, seconds=TOKEN_SECONDS)
        if done is None or done.returncode != 0:
            return None
        token = (done.stdout or "").strip()
        return token if _JWT.fullmatch(token) and not expired(token, self.clock()) else None

    def _signed_in(self) -> str:
        """Opens the browser sign-in, waits for the person, then reads the saved session."""
        log.info("graph-notes: no saved sign-in, opening the browser for %s", self.app_url)
        done = self._cloudflared("login", self.app_url, stdout=subprocess.DEVNULL, seconds=LOGIN_SECONDS)
        token = self._saved() if done is not None and done.returncode == 0 else None
        if token is None:
            raise SignInError(
                f"Signing in to {self.app_url} was not completed"
                + (f" within {LOGIN_SECONDS // 60} minutes" if done is None else "")
                + ". A browser window should have opened: finish signing in there, or run "
                f"`cloudflared access login {self.app_url}` in a terminal, then try again."
            )
        return token
