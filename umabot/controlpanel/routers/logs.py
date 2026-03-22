"""Log streaming via Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from umabot.controlpanel.deps import get_config

router = APIRouter(prefix="/logs", tags=["logs"])

# Patterns that look like secrets — redact before sending to the browser
_SECRET_PATTERNS = [
    re.compile(r'sk-proj-[A-Za-z0-9_-]{20,}'),          # OpenAI project keys
    re.compile(r'sk-[A-Za-z0-9]{48,}'),                   # OpenAI legacy keys
    re.compile(r'[0-9]{8,10}:[A-Za-z0-9_-]{35}'),         # Telegram bot tokens
    re.compile(r'(?i)(api[_-]?key|token|secret|password)=[^\s&"\']{8,}'),
]


def _redact(line: str) -> str:
    for pat in _SECRET_PATTERNS:
        line = pat.sub('[REDACTED]', line)
    return line


@router.get("/stream")
async def stream_logs(
    request: Request,
    config=Depends(get_config),
    level: str = "",
) -> StreamingResponse:
    """SSE endpoint that tails the umabot log file."""
    log_file = Path(config.runtime.log_dir) / "umabot.log"
    return StreamingResponse(
        _tail_log(request, log_file, level),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recent")
async def get_recent_logs(
    config=Depends(get_config),
    lines: int = 200,
    level: str = "",
) -> dict:
    """Return the last N log lines."""
    log_file = Path(config.runtime.log_dir) / "umabot.log"
    if not log_file.exists():
        return {"lines": [], "file": str(log_file)}
    all_lines = log_file.read_text(errors="replace").splitlines()
    tail = all_lines[-lines:]
    if level:
        level_upper = level.upper()
        tail = [l for l in tail if f"[{level_upper}]" in l]
    return {"lines": [_redact(l) for l in tail], "file": str(log_file)}


async def _tail_log(request: Request, log_file: Path, level: str) -> AsyncIterator[str]:
    """Async generator that tails a file and yields SSE events."""
    # Start from end of file
    offset = log_file.stat().st_size if log_file.exists() else 0
    level_upper = level.upper() if level else ""

    while not await request.is_disconnected():
        await asyncio.sleep(0.5)
        if not log_file.exists():
            continue
        size = log_file.stat().st_size
        if size <= offset:
            continue
        with open(log_file, "r", errors="replace") as f:
            f.seek(offset)
            new_content = f.read()
        offset = size
        for line in new_content.splitlines():
            if level_upper and f"[{level_upper}]" not in line:
                continue
            data = json.dumps({"line": _redact(line)})
            yield f"data: {data}\n\n"
