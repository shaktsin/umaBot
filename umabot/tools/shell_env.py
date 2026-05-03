from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import Dict


def merge_path_segments(preferred: str, fallback: str) -> str:
    """Merge PATH values preserving order and removing duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in (preferred or "", fallback or ""):
        for segment in raw.split(os.pathsep):
            seg = segment.strip()
            if not seg or seg in seen:
                continue
            seen.add(seg)
            out.append(seg)
    return os.pathsep.join(out)


@lru_cache(maxsize=1)
def zsh_login_path() -> str:
    """Resolve PATH from a login zsh shell (loads ~/.zshrc / ~/.zprofile)."""
    try:
        result = subprocess.run(
            ["/bin/zsh", "-lc", "printf %s \"$PATH\""],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def apply_zsh_path(env: Dict[str, str]) -> Dict[str, str]:
    """Return env with PATH preferring zsh login PATH when available."""
    merged = dict(env or {})
    zsh_path = zsh_login_path()
    if not zsh_path:
        return merged
    merged["PATH"] = merge_path_segments(zsh_path, merged.get("PATH", ""))
    return merged

