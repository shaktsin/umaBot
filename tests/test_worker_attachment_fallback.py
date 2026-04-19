from __future__ import annotations

from umabot.worker import _attachments_from_image_candidates, _extract_image_path_candidates


def test_extract_image_path_candidates_filters_urls_and_dedupes() -> None:
    text = (
        "Screenshot: `vaultly-landing.png`, "
        "again vaultly-landing.png, "
        "remote https://example.com/hero.png should be ignored, "
        "and nested path artifacts/shot.webp."
    )
    got = _extract_image_path_candidates(text)
    assert got == ["vaultly-landing.png", "artifacts/shot.webp"]


def test_attachments_from_image_candidates_resolves_under_roots(tmp_path) -> None:
    shot = tmp_path / "vaultly" / "vaultly-landing.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    attachments = _attachments_from_image_candidates(
        candidates=["vaultly-landing.png"],
        roots=[tmp_path],
    )

    assert len(attachments) == 1
    assert attachments[0].filename == "vaultly-landing.png"
    assert attachments[0].mime_type == "image/png"
    assert attachments[0].data.startswith(b"\x89PNG")
