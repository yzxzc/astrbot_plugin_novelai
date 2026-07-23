"""Download and query the local Danbooru vocabulary snapshot."""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

DATASET_API_URL = (
    "https://huggingface.co/api/datasets/HDiffusion/"
    "historical-danbooru-tag-counts/tree/main?recursive=true&expand=false"
)
DATASET_RAW_BASE_URL = (
    "https://huggingface.co/datasets/HDiffusion/"
    "historical-danbooru-tag-counts/resolve/main"
)
SNAPSHOT_PATTERN = re.compile(r"^danbooru-(\d{4}-\d{2}-\d{2})\.csv$")
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MIN_SNAPSHOT_TAGS = 100_000
SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class DanbooruCacheInfo:
    """Describe one validated local vocabulary database."""

    path: Path
    snapshot_date: str
    tag_count: int
    alias_count: int
    source_url: str


@dataclass(frozen=True)
class DanbooruTagMetadata:
    """Store fields required by strict local validation."""

    name: str
    post_count: int
    category: int


def default_cache_path() -> Path:
    """Return the per-user local vocabulary path.

    Returns:
        SQLite path under the current Windows user profile.
    """
    app_data = Path(os.environ.get("APPDATA", Path.home()))
    return app_data / "NAIPromptPlanner" / "danbooru-tags.sqlite3"


def read_cache_info(path: Path | None = None) -> DanbooruCacheInfo | None:
    """Read and validate local cache metadata.

    Args:
        path: Optional cache path override.

    Returns:
        Cache metadata, or ``None`` when the database is missing or invalid.
    """
    cache_path = path or default_cache_path()
    if not cache_path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        try:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if metadata.get("schema_version") != SCHEMA_VERSION:
                return None
            tag_count = int(metadata["tag_count"])
            alias_count = int(metadata["alias_count"])
            if tag_count < MIN_SNAPSHOT_TAGS:
                return None
            return DanbooruCacheInfo(
                path=cache_path,
                snapshot_date=metadata["snapshot_date"],
                tag_count=tag_count,
                alias_count=alias_count,
                source_url=metadata["source_url"],
            )
        finally:
            connection.close()
    except (KeyError, OSError, sqlite3.Error, ValueError):
        return None


def lookup_local_tags(
    names: set[str],
    path: Path | None = None,
) -> tuple[dict[str, str], dict[str, DanbooruTagMetadata]]:
    """Resolve aliases and load tag metadata from local SQLite.

    Args:
        names: Normalized underscore-separated candidate names.
        path: Optional cache path override.

    Returns:
        Original-to-canonical mapping and canonical metadata.

    Raises:
        FileNotFoundError: If no valid local vocabulary exists.
        sqlite3.Error: If the database cannot be queried.
    """
    cache_path = path or default_cache_path()
    if read_cache_info(cache_path) is None:
        raise FileNotFoundError(cache_path)
    ordered_names = sorted(names)
    placeholders = ",".join("?" for _ in ordered_names)
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        alias_rows = connection.execute(
            f"SELECT alias, tag_name FROM aliases WHERE alias IN ({placeholders})",
            ordered_names,
        )
        alias_map = {alias: tag_name for alias, tag_name in alias_rows}
        resolved = {name: alias_map.get(name, name) for name in ordered_names}
        canonical_names = sorted(set(resolved.values()))
        canonical_placeholders = ",".join("?" for _ in canonical_names)
        tag_rows = connection.execute(
            "SELECT name, post_count, category FROM tags "
            f"WHERE name IN ({canonical_placeholders})",
            canonical_names,
        )
        metadata = {
            name: DanbooruTagMetadata(name, post_count, category)
            for name, post_count, category in tag_rows
        }
        return resolved, metadata
    finally:
        connection.close()


async def update_danbooru_cache(
    path: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> DanbooruCacheInfo:
    """Download the latest daily snapshot and atomically build SQLite.

    Args:
        path: Optional destination path override.
        client: Optional injected HTTP client for tests.

    Returns:
        Metadata for the newly installed cache.

    Raises:
        RuntimeError: If the remote snapshot is malformed or incomplete.
        httpx.HTTPError: If the snapshot cannot be downloaded.
    """
    cache_path = path or default_cache_path()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        follow_redirects=True,
    )
    headers = {"User-Agent": "NAIPromptPlanner/0.1 (local tag cache updater)"}
    try:
        tree_response = await http_client.get(DATASET_API_URL, headers=headers)
        tree_response.raise_for_status()
        tree_payload = tree_response.json()
        if not isinstance(tree_payload, list):
            raise RuntimeError("标签快照目录响应无效。")
        snapshots: list[tuple[str, str]] = []
        for entry in tree_payload:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            match = SNAPSHOT_PATTERN.fullmatch(entry["path"])
            if match:
                snapshots.append((match.group(1), entry["path"]))
        if not snapshots:
            raise RuntimeError("没有找到可用的 Danbooru 每日标签快照。")
        snapshot_date, snapshot_name = max(snapshots)
        source_url = f"{DATASET_RAW_BASE_URL}/{snapshot_name}"
        snapshot_response = await http_client.get(source_url, headers=headers)
        snapshot_response.raise_for_status()
        content = snapshot_response.content
        if not content or len(content) > MAX_SNAPSHOT_BYTES:
            raise RuntimeError("Danbooru 标签快照大小异常。")
    finally:
        if owns_client:
            await http_client.aclose()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(".sqlite3.tmp")
    temporary_path.unlink(missing_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary_path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.executescript(
            """
            CREATE TABLE tags (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                post_count INTEGER NOT NULL,
                category INTEGER NOT NULL
            );
            CREATE TABLE aliases (
                alias TEXT PRIMARY KEY COLLATE NOCASE,
                tag_name TEXT NOT NULL
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        tag_count = 0
        alias_count = 0
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
        for row_number, row in enumerate(reader, 1):
            if len(row) != 4:
                raise RuntimeError(f"标签快照第 {row_number} 行字段数无效。")
            name = row[0].strip().casefold()
            try:
                category = int(row[1])
                post_count = int(row[2])
            except ValueError as exc:
                raise RuntimeError(f"标签快照第 {row_number} 行数值无效。") from exc
            if not name or post_count < 50:
                raise RuntimeError(f"标签快照第 {row_number} 行内容无效。")
            connection.execute(
                "INSERT INTO tags(name, post_count, category) VALUES (?, ?, ?)",
                (name, post_count, category),
            )
            tag_count += 1
            for alias in row[3].split(","):
                alias = alias.strip().casefold()
                if not alias or alias == name:
                    continue
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO aliases(alias, tag_name) VALUES (?, ?)",
                    (alias, name),
                )
                alias_count += cursor.rowcount
        if tag_count < MIN_SNAPSHOT_TAGS:
            raise RuntimeError("Danbooru 标签快照条目不足。")
        imported_at = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("schema_version", SCHEMA_VERSION),
                ("snapshot_date", snapshot_date),
                ("tag_count", str(tag_count)),
                ("alias_count", str(alias_count)),
                ("source_url", source_url),
                ("imported_at", imported_at),
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity != ("ok",):
            raise RuntimeError("本地 Danbooru 词库完整性检查失败。")
        connection.close()
        connection = None
        os.replace(temporary_path, cache_path)
    except Exception:
        if connection is not None:
            connection.close()
        temporary_path.unlink(missing_ok=True)
        raise

    info = read_cache_info(cache_path)
    if info is None:
        raise RuntimeError("本地 Danbooru 词库安装后校验失败。")
    return info
