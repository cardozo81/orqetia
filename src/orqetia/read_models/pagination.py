"""Opaque cursor pagination helpers; cursors never carry authorization state."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CursorPage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


def encode_cursor(*, last_key: str, query_fingerprint: str) -> str:
    payload = json.dumps(
        {"v": 1, "last": last_key, "query": query_fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(*, cursor: str, query_fingerprint: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(decoded)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported cursor version")
    if payload.get("query") != query_fingerprint:
        raise ValueError("cursor query fingerprint mismatch")
    last = payload.get("last")
    if not isinstance(last, str) or not last:
        raise ValueError("cursor last key is invalid")
    return last


def paginate(
    items: Sequence[T],
    *,
    key: Callable[[T], str],
    page_size: int,
    query_fingerprint: str,
    cursor: str | None = None,
    maximum_page_size: int = 200,
) -> CursorPage[T]:
    if page_size < 1 or page_size > maximum_page_size:
        raise ValueError("page_size outside allowed range")
    ordered = sorted(items, key=key)
    start = 0
    if cursor is not None:
        last = decode_cursor(cursor=cursor, query_fingerprint=query_fingerprint)
        while start < len(ordered) and key(ordered[start]) <= last:
            start += 1
    page_items = tuple(ordered[start : start + page_size])
    if start + page_size >= len(ordered) or not page_items:
        next_cursor = None
    else:
        next_cursor = encode_cursor(
            last_key=key(page_items[-1]),
            query_fingerprint=query_fingerprint,
        )
    return CursorPage(page_items, next_cursor)
