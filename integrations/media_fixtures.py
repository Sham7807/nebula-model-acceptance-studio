"""Verified public media for URL and Base64 acceptance probes.

URL probes always submit the original public address. Base64 probes use the
bundled, hash-verified bytes downloaded from that same address, so CDN access
problems never replace the photograph or video with an unrelated fixture.
Plan previews are offline and deterministic, as are the generated requests.
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

IMAGE_URL = "https://picsum.photos/id/237/800/600"
VIDEO_URL = "https://download.samplelib.com/mp4/sample-5s.mp4"
AUDIO_URL = "https://download.samplelib.com/mp3/sample-3s.mp3"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "multimodal-workbench/fixtures/public-media"
SOURCES = {"image": IMAGE_URL, "video": VIDEO_URL, "audio": AUDIO_URL}


@lru_cache(maxsize=3)
def _fixture(kind: str) -> tuple[bytes, dict]:
    if kind not in SOURCES:
        raise ValueError("kind must be image, video or audio")
    manifest = json.loads((FIXTURE_ROOT / "sources.json").read_text(encoding="utf-8"))
    meta = manifest[kind]
    name = str(meta["file"])
    if Path(name).name != name or meta["url"] != SOURCES[kind]:
        raise ValueError("真实媒体样例来源清单无效")
    payload = (FIXTURE_ROOT / name).read_bytes()
    maximum = {"image": MAX_IMAGE_BYTES, "video": MAX_VIDEO_BYTES, "audio": MAX_AUDIO_BYTES}[kind]
    if not payload or len(payload) > maximum or len(payload) != meta["size"]:
        raise ValueError("真实媒体样例大小校验失败：" + kind)
    if hashlib.sha256(payload).hexdigest() != meta["sha256"]:
        raise ValueError("真实媒体样例 SHA-256 校验失败：" + kind)
    if not str(meta["mime"]).startswith(kind + "/"):
        raise ValueError("真实媒体样例 MIME 不匹配：" + kind)
    return payload, meta


def data_url(kind: str, *, fetch: bool = True) -> tuple[str, bool]:
    """Return the real URL's verified bytes; ``fetch`` is kept for callers.

    The boolean indicates real public-source bytes, not a live download during
    this run. The provenance field from variants identifies the bundled copy.
    Missing/corrupt assets raise explicitly; there is no synthetic fallback.
    """
    payload, meta = _fixture(kind)
    return "data:%s;base64,%s" % (meta["mime"], base64.b64encode(payload).decode("ascii")), True


def variants(kind: str, *, fetch: bool = True) -> list[dict[str, object]]:
    """URL and Base64 forms with the actual source, MIME, size and digest."""
    payload, meta = _fixture(kind)
    value, _ = data_url(kind, fetch=fetch)
    provenance = {"remote": True, "source_url": SOURCES[kind], "mime": meta["mime"],
                  "bytes": len(payload), "sha256": meta["sha256"]}
    return [
        {**provenance, "encoding": "url", "value": SOURCES[kind], "source": "public_url"},
        {**provenance, "encoding": "base64", "value": value, "source": "verified_bundled_copy"},
    ]


__all__ = ["IMAGE_URL", "VIDEO_URL", "AUDIO_URL", "MAX_IMAGE_BYTES", "MAX_VIDEO_BYTES", "MAX_AUDIO_BYTES", "data_url", "variants"]
