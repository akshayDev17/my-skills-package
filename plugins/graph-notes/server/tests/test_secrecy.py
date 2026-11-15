"""The session is sent as a request header and appears nowhere else: not in a tool
result, an error, a log line, anything written to stdout or stderr, or a command line."""

from __future__ import annotations

import logging
import subprocess

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp import client
from support import App, fake_session, tour


def everything_said(results: list, caplog, capsys) -> str:
    """All a harness, a terminal or a log file could have seen."""
    out = capsys.readouterr()
    logs = "\n".join(f"{r.name} {r.getMessage()} {r.args!r} {r.exc_text or ''}" for r in caplog.records)
    return "\n".join([*(repr(r) for r in results), logs, out.out, out.err])


def test_a_tour_of_every_tool_and_every_failure_never_shows_the_session(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    app = App()
    results = tour(app)
    said = everything_said(results, caplog, capsys)
    assert len(said) > 5000 and "Tour, renamed" in said and "Nothing was saved" in said  # it did capture them
    assert app.session_value not in said
    assert app.session_value.split(".")[1] not in said and app.session_value.split(".")[2] not in said
    assert "cf-access-token" not in said and "cf-access-jwt-assertion" not in said
    assert all(app.session_value not in " ".join(argv) for argv, _ in app.cloudflared.calls)


def test_signing_in_again_mid_use_never_shows_either_session(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    app = App(signed_in=False)
    results = [app.result("create_graph", title="Plan")]
    first = app.cloudflared.saved
    app.edge.sessions.clear()
    results.append(app.result("add_notes", slug="plan", version=1, notes=[{"text": "n"}]))
    second = app.cloudflared.saved
    app.edge.sessions.clear()
    app.cloudflared.edge = type("NoEdge", (), {"sessions": set()})()
    results.append(app.result("list_graphs"))  # refused twice
    assert [r.is_error for r in results] == [False, False, True]
    said = everything_said(results, caplog, capsys)
    assert first != second and app.cloudflared.logins == 3
    for session in (first, second, app.cloudflared.saved):
        assert session not in said and session.split(".")[2] not in said
        assert all(session not in " ".join(argv) for argv, _ in app.cloudflared.calls)


def test_a_failure_that_quotes_the_session_is_redacted_before_it_reaches_the_model():
    """Nothing puts the session in an error; if a future change did, it would still not get out."""
    app = App()
    app.call("list_graphs")

    def leaky(method, url, headers, body):
        raise client.ApiError(0, f"could not send {headers}")

    object.__setattr__(app.api, "send", leaky)
    with pytest.raises(ToolError) as failure:
        app.call("list_graphs")
    assert app.session_value not in str(failure.value) and "[session]" in str(failure.value)


def test_an_unexpected_crash_inside_a_tool_does_not_show_the_session(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    app = App()
    app.call("list_graphs")

    def crash(method, url, headers, body):
        raise RuntimeError("the sender broke")

    object.__setattr__(app.api, "send", crash)
    result = app.result("list_graphs")
    assert result.is_error
    assert app.session_value not in everything_said([result], caplog, capsys)


def test_cloudflareds_own_output_is_never_passed_on(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    app = App(signed_in=False)
    printed = fake_session("printed-by-login")

    def cloudflared(argv, **kwargs):
        if argv[2] == "token":
            return subprocess.CompletedProcess(argv, 1, stdout="")
        # What `access login` prints when it works, here in a run that then times out.
        raise subprocess.TimeoutExpired(argv, 1, output=f"Successfully fetched your token:\n\n{printed}")

    app.api.session.run = cloudflared
    result = app.result("list_graphs")
    assert result.is_error
    assert printed not in everything_said([result], caplog, capsys)
