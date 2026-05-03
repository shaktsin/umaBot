from __future__ import annotations

from umabot.tools import shell_env


def test_merge_path_segments_deduplicates_and_preserves_order() -> None:
    merged = shell_env.merge_path_segments(
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin",
        "/usr/local/bin:/bin:/usr/bin",
    )
    assert merged == "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def test_apply_zsh_path_prefers_zsh_path(monkeypatch) -> None:
    monkeypatch.setattr(shell_env, "zsh_login_path", lambda: "/opt/homebrew/bin:/usr/local/bin")
    env = {"PATH": "/usr/local/bin:/usr/bin"}
    out = shell_env.apply_zsh_path(env)
    assert out["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/usr/bin"

