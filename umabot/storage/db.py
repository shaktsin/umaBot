from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connector TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES messages(id)
                );
                CREATE TABLE IF NOT EXISTS message_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    data_b64 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES messages(id)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS queue_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_until TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS connector_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connector TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connector_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connector TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    session_data BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    task_type TEXT NOT NULL, -- one_time | periodic
                    schedule_json TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    status TEXT NOT NULL DEFAULT 'active', -- active | completed | cancelled
                    next_run_at TEXT,
                    lease_until TEXT,
                    last_run_at TEXT,
                    last_result TEXT,
                    last_error TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    status TEXT NOT NULL, -- running | success | failed
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    result TEXT,
                    error TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL UNIQUE,
                    token_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gmail_imap_state (
                    connector TEXT PRIMARY KEY,
                    last_uid INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self._ensure_session_connector()
        self._ensure_audit_columns()

    def close(self) -> None:
        self._conn.close()

    def get_or_create_session(self, chat_id: str, channel: str, connector: str = "") -> int:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE chat_id = ? AND channel = ? AND connector = ?",
                (chat_id, channel, connector),
            ).fetchone()
            if row:
                return int(row["id"])
            now = _now()
            cur = self._conn.execute(
                "INSERT INTO sessions (connector, chat_id, channel, created_at) VALUES (?, ?, ?, ?)",
                (connector, chat_id, channel, now),
            )
            return int(cur.lastrowid)

    def add_message(self, session_id: int, role: str, content: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )
            return int(cur.lastrowid)

    def list_recent_messages(self, session_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        messages = [{"role": row["role"], "content": row["content"]} for row in rows]
        return list(reversed(messages))

    def add_tool_call(self, message_id: int, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tool_calls (message_id, tool_name, args_json, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, tool_name, json.dumps(args), json.dumps(result), _now()),
            )

    def add_message_attachments(self, message_id: int, attachments: List[Dict[str, Any]]) -> None:
        """Persist already-serialized attachments for a message.

        Expected attachment shape:
          {"filename": str, "mime_type": str, "data": "<base64>"}
        """
        if not attachments:
            return
        rows = []
        now = _now()
        for att in attachments:
            filename = str(att.get("filename", "")).strip()
            mime_type = str(att.get("mime_type", "")).strip()
            data_b64 = str(att.get("data", "")).strip()
            if not filename or not mime_type or not data_b64:
                continue
            rows.append((message_id, filename, mime_type, data_b64, now))
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO message_attachments (message_id, filename, mime_type, data_b64, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_message_attachments(self, message_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT filename, mime_type, data_b64
                FROM message_attachments
                WHERE message_id = ?
                ORDER BY id
                """,
                (message_id,),
            ).fetchall()
        return [
            {
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "data": row["data_b64"],
            }
            for row in rows
        ]

    def add_audit(
        self,
        event_type: str,
        details: Dict[str, Any],
        *,
        user_id: str = "",
        connector: str = "",
        chat_id: str = "",
        decision: str = "",
    ) -> None:
        payload = dict(details)
        if user_id:
            payload["user_id"] = user_id
        if connector:
            payload["connector"] = connector
        if chat_id:
            payload["chat_id"] = chat_id
        if decision:
            payload["decision"] = decision
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (event_type, details_json, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload), _now()),
            )

    def update_connector_status(self, connector: str, channel: str, mode: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO connector_status (connector, channel, mode, status, updated_at) VALUES (?, ?, ?, ?, ?)",
                (connector, channel, mode, status, _now()),
            )

    def upsert_connector_session(self, connector: str, session_type: str, session_data: bytes) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM connector_sessions WHERE connector = ? AND session_type = ?",
                (connector, session_type),
            )
            self._conn.execute(
                "INSERT INTO connector_sessions (connector, session_type, session_data, updated_at) VALUES (?, ?, ?, ?)",
                (connector, session_type, session_data, _now()),
            )

    def get_connector_session(self, connector: str, session_type: str) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_data FROM connector_sessions WHERE connector = ? AND session_type = ? ORDER BY id DESC LIMIT 1",
                (connector, session_type),
            ).fetchone()
        if not row:
            return None
        return row["session_data"]

    # ------------------------------------------------------------------
    # OAuth token storage
    # ------------------------------------------------------------------

    def store_oauth_token(self, provider: str, token_json: str) -> None:
        """Upsert an OAuth token JSON blob for the given provider."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO oauth_tokens (provider, token_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(provider) DO UPDATE
                    SET token_json = excluded.token_json,
                        updated_at = excluded.updated_at
                """,
                (provider, token_json, _now()),
            )

    def get_oauth_token(self, provider: str) -> Optional[str]:
        """Return the raw token JSON for provider, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT token_json FROM oauth_tokens WHERE provider = ?",
                (provider,),
            ).fetchone()
        return row["token_json"] if row else None

    def delete_oauth_token(self, provider: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM oauth_tokens WHERE provider = ?", (provider,)
            )

    # ------------------------------------------------------------------
    # Gmail IMAP state
    # ------------------------------------------------------------------

    def get_gmail_imap_last_uid(self, connector: str) -> int:
        """Return last seen IMAP UID for connector (0 if never seen)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_uid FROM gmail_imap_state WHERE connector = ?",
                (connector,),
            ).fetchone()
        return int(row["last_uid"]) if row else 0

    def set_gmail_imap_last_uid(self, connector: str, uid: int) -> None:
        """Persist last seen IMAP UID for connector."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO gmail_imap_state (connector, last_uid, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(connector) DO UPDATE
                    SET last_uid = excluded.last_uid,
                        updated_at = excluded.updated_at
                """,
                (connector, uid, _now()),
            )

    # ------------------------------------------------------------------
    # Schema migrations
    # ------------------------------------------------------------------

    def _ensure_session_connector(self) -> None:
        with self._lock, self._conn:
            cols = self._conn.execute("PRAGMA table_info(sessions)").fetchall()
            names = {row[1] for row in cols}
            if "connector" not in names:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN connector TEXT NOT NULL DEFAULT ''")

    def _ensure_audit_columns(self) -> None:
        """No-op: audit_log uses a flexible JSON details column; no migration needed."""
        pass

    def enqueue_job(self, chat_id: str, channel: str, payload: Dict[str, Any]) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO queue_jobs (chat_id, channel, payload_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, channel, json.dumps(payload), "queued", _now(), _now()),
            )
            return int(cur.lastrowid)

    def claim_job(self, lease_seconds: int = 30) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn:
            now = _now()
            lease_until = _future(lease_seconds)
            row = self._conn.execute(
                "SELECT id FROM queue_jobs WHERE status = 'queued' OR (status = 'leased' AND lease_until < ?) ORDER BY id LIMIT 1",
                (now,),
            ).fetchone()
            if not row:
                return None
            job_id = int(row["id"])
            self._conn.execute(
                "UPDATE queue_jobs SET status = 'leased', lease_until = ?, updated_at = ?, attempts = attempts + 1 WHERE id = ?",
                (lease_until, now, job_id),
            )
            job = self._conn.execute(
                "SELECT * FROM queue_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not job:
                return None
            return dict(job)

    def complete_job(self, job_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE queue_jobs SET status = 'done', updated_at = ? WHERE id = ?",
                (_now(), job_id),
            )

    def fail_job(self, job_id: int, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE queue_jobs SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
                (error, _now(), job_id),
            )

    def create_task(
        self,
        *,
        name: str,
        prompt: str,
        task_type: str,
        schedule: Dict[str, Any],
        timezone: str,
        next_run_at: Optional[str],
        created_by: str,
    ) -> int:
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO tasks (
                    name, prompt, task_type, schedule_json, timezone, status,
                    next_run_at, lease_until, last_run_at, last_result, last_error,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    name,
                    prompt,
                    task_type,
                    json.dumps(schedule),
                    timezone,
                    next_run_at,
                    created_by,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [self._task_row_to_dict(row) for row in rows]

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return self._task_row_to_dict(row)

    def cancel_task(self, task_id: int) -> bool:
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'cancelled', lease_until = NULL, updated_at = ? WHERE id = ? AND status != 'cancelled'",
                (now, task_id),
            )
            return int(cur.rowcount) > 0

    def lease_due_tasks(self, *, limit: int = 20, lease_seconds: int = 30) -> List[Dict[str, Any]]:
        now = _now()
        lease_until = _future(lease_seconds)
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT id FROM tasks
                WHERE status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                  AND (lease_until IS NULL OR lease_until < ?)
                ORDER BY next_run_at ASC, id ASC
                LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            task_ids = [int(row["id"]) for row in rows]
            if not task_ids:
                return []
            for task_id in task_ids:
                self._conn.execute(
                    "UPDATE tasks SET lease_until = ?, updated_at = ? WHERE id = ?",
                    (lease_until, now, task_id),
                )
            leased = self._conn.execute(
                f"SELECT * FROM tasks WHERE id IN ({','.join('?' for _ in task_ids)}) ORDER BY next_run_at ASC, id ASC",
                tuple(task_ids),
            ).fetchall()
        return [self._task_row_to_dict(row) for row in leased]

    def create_task_run(self, task_id: int) -> int:
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO task_runs (task_id, status, started_at) VALUES (?, 'running', ?)",
                (task_id, now),
            )
            self._conn.execute(
                "UPDATE tasks SET last_run_at = ?, updated_at = ? WHERE id = ?",
                (now, now, task_id),
            )
            return int(cur.lastrowid)

    def complete_task_run(
        self,
        *,
        run_id: int,
        task_id: int,
        result: str,
        next_run_at: Optional[str],
        terminal: bool,
    ) -> None:
        now = _now()
        new_status = "completed" if terminal else "active"
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE task_runs SET status = 'success', finished_at = ?, result = ? WHERE id = ?",
                (now, result, run_id),
            )
            self._conn.execute(
                """
                UPDATE tasks
                SET status = ?, next_run_at = ?, lease_until = NULL, last_result = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_status, next_run_at, result, now, task_id),
            )

    def fail_task_run(self, *, run_id: int, task_id: int, error: str, next_run_at: Optional[str] = None) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE task_runs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                (now, error, run_id),
            )
            self._conn.execute(
                """
                UPDATE tasks
                SET status = 'active', next_run_at = COALESCE(?, next_run_at), lease_until = NULL, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_run_at, error, now, task_id),
            )

    def release_task_lease(self, task_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET lease_until = NULL, updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )

    def _task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        try:
            data["schedule"] = json.loads(data.pop("schedule_json") or "{}")
        except json.JSONDecodeError:
            data["schedule"] = {}
        return data


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _future(seconds: int) -> str:
    return (datetime.utcnow() + timedelta(seconds=seconds)).isoformat() + "Z"
