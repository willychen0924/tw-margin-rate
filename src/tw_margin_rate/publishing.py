"""Publishing safeguards for the official GitHub repository."""

from __future__ import annotations


OFFICIAL_ORIGIN_URLS = frozenset(
    {
        "https://github.com/willychen0924/tw-margin-rate.git",
        "git@github.com:willychen0924/tw-margin-rate.git",
    }
)


def assert_official_origin(remote: str) -> None:
    """Accept only the exact HTTPS or SSH URL for the official repository."""
    if remote not in OFFICIAL_ORIGIN_URLS:
        raise RuntimeError(f"origin 不是指定公開儲存庫：{remote}")
