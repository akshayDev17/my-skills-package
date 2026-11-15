"""Signing in: the saved cloudflared session is reused, the browser sign-in starts only
when there is none (or it expired), and a refusal mid-use leads to one fresh sign-in
and one retry."""

from __future__ import annotations

import subprocess
import time

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp.server import search_path
from graph_notes_mcp.session import LOGIN_SECONDS, Session, expired
from support import APP_URL, App, fake_session

TOKEN = ["cloudflared", "access", "token", f"-app={APP_URL}"]
LOGIN = ["cloudflared", "access", "login", APP_URL]


def commands(app: App) -> list[list[str]]:
    return [argv for argv, _ in app.cloudflared.calls]


def test_a_saved_session_is_used_without_any_browser_and_fetched_once():
    app = App()
    app.call("list_graphs")
    app.call("create_graph", title="Plan")
    app.call("list_graphs")
    assert commands(app) == [TOKEN]
    assert all(headers["cf-access-token"] == app.session_value for _, _, headers, _ in app.edge.requests)


def test_with_no_saved_session_the_browser_sign_in_runs_then_the_call_continues():
    app = App(signed_in=False)
    assert app.call("list_graphs") == {"mine": [], "public_from_others": []}
    assert commands(app) == [TOKEN, LOGIN, TOKEN]
    assert len(app.edge.requests) == 1  # nothing was sent before there was a session


def test_an_expired_saved_session_is_not_sent_the_sign_in_runs_instead():
    app = App(signed_in=False)
    app.cloudflared.saved = fake_session("stale", expires_in=-60)
    app.call("list_graphs")
    assert commands(app) == [TOKEN, LOGIN, TOKEN]
    assert [headers["cf-access-token"] for _, _, headers, _ in app.edge.requests] == [app.cloudflared.saved]


def test_a_session_that_expires_while_the_server_runs_is_renewed_before_the_next_call():
    app = App()
    now = [1_000_000_000.0]  # a clock the test moves: the first session's `exp` is an hour after "now"
    app.api.session.clock = lambda: now[0]
    first = fake_session("early", expires_in=3600 - (time.time() - now[0]))
    app.cloudflared.saved = first
    app.edge.sessions.add(first)
    app.call("list_graphs")
    now[0] += 3601  # the hour passes; cloudflared's copy is the same expired one
    app.call("list_graphs")
    assert commands(app) == [TOKEN, TOKEN, LOGIN, TOKEN]
    assert app.edge.requests[-1][2]["cf-access-token"] == app.cloudflared.saved != first


def test_a_saved_session_in_its_last_seconds_is_still_used_without_a_browser():
    app = App(signed_in=False)
    last_seconds = fake_session("last-seconds", expires_in=10)  # cloudflared's `login` would hand back this same one
    app.cloudflared.saved = last_seconds
    app.edge.sessions.add(last_seconds)
    app.call("list_graphs")
    assert commands(app) == [TOKEN] and app.cloudflared.logins == 0
    assert app.edge.requests[-1][2]["cf-access-token"] == last_seconds


def test_cloudflared_missing_says_how_to_install_it():
    app = App()
    app.cloudflared.installed = False
    with pytest.raises(ToolError) as refused:
        app.call("list_graphs")
    message = str(refused.value)
    assert "`cloudflared`, which is not installed" in message and "brew install cloudflared" in message
    assert "developers.cloudflare.com" in message
    assert app.edge.requests == []


def test_cloudflared_is_also_looked_for_where_installers_put_it_after_the_path_as_given():
    assert search_path("/usr/bin:/bin") == "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"
    assert search_path("/opt/homebrew/bin:/usr/bin") == "/opt/homebrew/bin:/usr/bin:/usr/local/bin"
    assert search_path("") == "/opt/homebrew/bin:/usr/local/bin"  # never an empty entry: that is the current directory
    assert search_path(":/usr/bin::") == "/usr/bin:/opt/homebrew/bin:/usr/local/bin"


def test_a_sign_in_the_person_never_finishes_ends_with_what_to_do():
    app = App(signed_in=False)
    app.cloudflared.login_works = False
    with pytest.raises(ToolError) as refused:
        app.call("list_graphs")
    message = str(refused.value)
    assert f"was not completed within {LOGIN_SECONDS // 60} minutes" in message
    assert f"`cloudflared access login {APP_URL}`" in message
    assert "Please open the following URL" not in message  # cloudflared's own output is never passed on
    assert commands(app) == [TOKEN, LOGIN] and app.edge.requests == []


def test_a_refusal_mid_use_leads_to_one_fresh_sign_in_and_one_retry():
    app = App()
    app.call("create_graph", title="Plan")
    app.edge.sessions.clear()  # Access ends the session; cloudflared still has the old one saved
    added = app.call("add_notes", slug="plan", version=1, notes=[{"text": "after signing in again"}])
    assert added["version"] == 2
    assert commands(app) == [TOKEN, TOKEN, LOGIN, TOKEN] and app.cloudflared.logins == 1
    statuses = [(method, headers["cf-access-token"] == app.session_value) for method, _, headers, _ in app.edge.requests[1:]]
    assert statuses == [("GET", True), ("GET", False), ("PUT", False)]  # refused once, retried once, then on


def test_a_newer_saved_session_is_picked_up_after_a_refusal_without_a_browser():
    app = App()
    app.call("list_graphs")
    fresh = fake_session("from-a-terminal")  # the person ran `cloudflared access login` themselves
    app.edge.sessions = {fresh}
    app.cloudflared.saved = fresh
    app.call("list_graphs")
    assert commands(app) == [TOKEN, TOKEN] and app.cloudflared.logins == 0


def test_a_sign_in_that_is_refused_again_is_reported_not_retried_in_a_loop():
    app = App()
    app.call("list_graphs")
    app.edge.sessions.clear()
    app.cloudflared.edge = type("NoEdge", (), {"sessions": set()})()  # the new session is refused as well
    sent = len(app.edge.requests)
    with pytest.raises(ToolError) as refused:
        app.call("list_graphs")
    assert "refused a fresh sign-in as well" in str(refused.value) and APP_URL in str(refused.value)
    assert app.cloudflared.logins == 1 and len(app.edge.requests) == sent + 2


def test_a_refused_session_that_has_not_expired_is_removed_so_signing_in_gives_a_new_one():
    app = App()
    app.call("list_graphs")
    app.edge.sessions.clear()  # Access refuses a session cloudflared still counts as valid
    app.call("list_graphs")
    assert commands(app) == [TOKEN, TOKEN, LOGIN, TOKEN] and app.cloudflared.logins == 1
    assert app.cloudflared.saved not in (None, app.session_value)
    assert app.edge.requests[-1][2]["cf-access-token"] == app.cloudflared.saved


def test_a_refused_session_cloudflared_keeps_handing_back_is_reported_once_without_it():
    app = App()
    app.call("list_graphs")
    app.api.session.store = app.api.session.store / "elsewhere"  # cloudflared keeps its copy where it cannot be removed
    app.edge.sessions.clear()
    sent = len(app.edge.requests)
    with pytest.raises(ToolError) as refused:
        app.call("list_graphs")
    message = str(refused.value)
    assert "handed back that same session" in message and "Wait a minute" in message
    assert app.session_value not in message and app.session_value.split(".")[2] not in message
    assert app.cloudflared.logins == 1 and len(app.edge.requests) == sent + 1


def test_cloudflared_gets_no_terminal_and_only_the_token_command_is_read():
    app = App(signed_in=False)
    app.call("list_graphs")
    for argv, kwargs in app.cloudflared.calls:
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdout"] == (subprocess.PIPE if argv[2] == "token" else subprocess.DEVNULL)
        assert kwargs["timeout"] > 0 and "shell" not in kwargs and "env" not in kwargs


def test_output_that_is_not_a_session_is_never_sent_as_one():
    app = App(signed_in=False)
    app.cloudflared.saved = "Unable to find token for provided application.\r\ninjected: header"
    app.cloudflared.login_works = False
    with pytest.raises(ToolError, match="was not completed"):
        app.call("list_graphs")
    assert app.edge.requests == []


def test_expired_reads_the_sessions_own_expiry():
    assert expired(fake_session("old", expires_in=-1), now=time.time())
    assert not expired(fake_session("new", expires_in=600), now=time.time())
    assert not expired(fake_session("ending", expires_in=10), now=time.time())
    assert expired(fake_session("ending", expires_in=10), now=time.time(), leeway=30)
    assert not expired("not.a.jwt", now=0) and not expired("opaque", now=0)  # unreadable: the app decides


def test_the_session_object_does_not_show_its_secret():
    app = App()
    app.call("list_graphs")
    assert app.session_value not in repr(app.api.session) and app.session_value not in repr(app.api)
    assert repr(Session(APP_URL)).startswith("Session(app_url='https://graph.test/graph'")
