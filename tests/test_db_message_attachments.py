from __future__ import annotations

from umabot.storage.db import Database


def test_message_attachments_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "umabot.db"
    db = Database(str(db_path))
    try:
        session_id = db.get_or_create_session("admin", "web", "web-panel")
        message_id = db.add_message(session_id, "assistant", "with image")
        db.add_message_attachments(
            message_id,
            [
                {
                    "filename": "landing.png",
                    "mime_type": "image/png",
                    "data": "ZmFrZS1iYXNlNjQ=",
                }
            ],
        )
        got = db.get_message_attachments(message_id)
        assert len(got) == 1
        assert got[0]["filename"] == "landing.png"
        assert got[0]["mime_type"] == "image/png"
        assert got[0]["data"] == "ZmFrZS1iYXNlNjQ="
    finally:
        db.close()
