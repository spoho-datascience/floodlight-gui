"""Runtime capability flags for optional dependencies. Single source of truth.

Probed eagerly at module import time. Importing this module triggers the
imageio + ffmpeg-plugin discovery check exactly once per process. If the
check fails for any reason, ``HAS_FFMPEG`` is set to ``False``; no exception
propagates to the caller.

DPG-free: safe to import from backend modules (``core/``, ``registry/``).
"""

from __future__ import annotations

import logging

__all__ = ["HAS_FFMPEG"]

logger = logging.getLogger(__name__)


def _probe_imageio_ffmpeg() -> bool:
    """Return True only when both imageio and imageio_ffmpeg import successfully.

    Both packages must be present: imageio alone does not guarantee that the
    ffmpeg plugin is available, so probing only imageio would incorrectly
    enable the video-export path in environments that lack the codec.
    """
    try:
        import imageio  # noqa: F401
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:
        return False


# HAS_FFMPEG: True when imageio and imageio_ffmpeg are both importable.
# Gates video-export functionality throughout the application. Evaluated once
# at module import time; the result is read-only for the remainder of the
# process lifetime.
HAS_FFMPEG: bool = _probe_imageio_ffmpeg()
logger.debug("ffmpeg capability probe: HAS_FFMPEG=%s", HAS_FFMPEG)
