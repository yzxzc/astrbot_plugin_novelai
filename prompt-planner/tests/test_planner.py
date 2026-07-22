"""Tests for the standalone DeepSeek prompt planner."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from nai_prompt_planner.planner import (
    DeepSeekPromptPlanner,
    PlannerError,
    PlannerSettings,
    load_system_prompt,
    parse_planner_response,
)
from nai_prompt_planner.tag_cache import lookup_local_tags, update_danbooru_cache


def _write_tag_cache(
    path: Path,
    names: set[str],
    aliases: dict[str, str] | None = None,
) -> Path:
    """Create a minimal valid local vocabulary for planner tests.

    Args:
        path: SQLite destination.
        names: Canonical names to store.
        aliases: Optional alias-to-canonical mapping.

    Returns:
        Created cache path.
    """
    aliases = aliases or {}
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE tags (name TEXT PRIMARY KEY, post_count INTEGER, category INTEGER);
        CREATE TABLE aliases (alias TEXT PRIMARY KEY, tag_name TEXT);
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    connection.executemany(
        "INSERT INTO tags VALUES (?, 1000, 0)",
        ((name,) for name in names),
    )
    connection.executemany(
        "INSERT INTO aliases VALUES (?, ?)",
        aliases.items(),
    )
    connection.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        (
            ("schema_version", "1"),
            ("snapshot_date", "2026-07-22"),
            ("tag_count", "100000"),
            ("alias_count", str(len(aliases))),
            ("source_url", "https://example.test/danbooru.csv"),
        ),
    )
    connection.commit()
    connection.close()
    return path


def test_gui_packaged_resource_self_test() -> None:
    """Keep the hidden EXE self-test independent from Tk window creation."""
    from nai_prompt_planner.gui import _self_test

    assert _self_test() == 0


def test_load_system_prompt_uses_content_first_contract() -> None:
    """Bundle the actual content-first runtime instructions."""
    system_prompt = load_system_prompt()

    assert "短或稀疏描述" in system_prompt
    assert "不以标签数量为目标" in system_prompt
    assert "character_prompts" in system_prompt
    assert "qualityToggle" in system_prompt


def test_parse_response_keeps_exact_character_slot_contract() -> None:
    """Accept dynamic captions only under the original protected slot key."""
    slot = "__NAI_CHARACTER_SLOT_1__"
    raw_response = json.dumps(
        {
            "ok": True,
            "prompt": "1person, solo, eating ice cream",
            "character_prompts": {slot: "holding ice cream cone, taking a bite, happy"},
            "error": None,
        }
    )

    result = parse_planner_response(raw_response, 4000, (slot,))

    assert result["ok"] is True
    assert result["character_prompts"][slot].startswith("holding ice cream")


@pytest.mark.parametrize(
    "prompt",
    [
        "1girl, solo, best quality",
        "1girl, artist:example",
        "1girl, solo, 标签",
        "```1girl, solo```",
        "Prompt: 1girl, solo",
    ],
)
def test_parse_response_rejects_managed_or_non_english_output(prompt: str) -> None:
    """Reject output that the caller or NovelAI quality toggle owns."""
    raw_response = json.dumps(
        {
            "ok": True,
            "prompt": prompt,
            "character_prompts": {},
            "error": None,
        }
    )

    with pytest.raises(PlannerError, match="Prompt"):
        parse_planner_response(raw_response, 4000)


def test_failure_response_requires_the_exact_protocol() -> None:
    """Reject incomplete model failure objects."""
    with pytest.raises(PlannerError, match="字段"):
        parse_planner_response(
            '{"ok":false,"prompt":null,"error":"conflicting_constraints"}',
            4000,
        )


@pytest.mark.asyncio
async def test_deepseek_request_uses_json_mode_and_non_thinking_flash() -> None:
    """Send the low-latency official DeepSeek request shape by default."""
    captured_body: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"ok":true,"prompt":"1girl, solo, cute, '
                                'layered dress, puff sleeves, gentle smile",'
                                '"character_prompts":{},"error":null}'
                            )
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client=client,
    )

    result = await planner.plan("可爱的女孩")
    await client.aclose()

    assert result["ok"] is True
    assert captured_body["model"] == "deepseek-v4-flash"
    assert captured_body["thinking"] == {"type": "disabled"}
    assert captured_body["response_format"] == {"type": "json_object"}
    assert captured_body["temperature"] == 0


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_at_most_twice() -> None:
    """Bound malformed-output repair to three total DeepSeek calls."""
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "not-json"},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client=client,
    )

    with pytest.raises(PlannerError, match="JSON"):
        await planner.plan("可爱的女孩")
    await client.aclose()

    assert request_count == 3


@pytest.mark.asyncio
async def test_empty_deepseek_content_uses_the_same_bounded_repair() -> None:
    """Retry the documented empty-content JSON-mode failure without looping."""
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        content = (
            ""
            if request_count < 3
            else (
                '{"ok":true,"prompt":"1girl, solo, layered dress, gentle smile",'
                '"character_prompts":{},"error":null}'
            )
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": content},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client=client,
    )

    result = await planner.plan("可爱的女孩")
    await client.aclose()

    assert result["ok"] is True
    assert request_count == 3


@pytest.mark.asyncio
async def test_invented_english_phrase_is_repaired_before_return(
    tmp_path: Path,
) -> None:
    """Never return a readable English phrase that is not an exact tag."""
    deepseek_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deepseek_count
        deepseek_count += 1
        prompt = (
            "1girl, solo, ancient Chinese knight-errant, holding sword"
            if deepseek_count == 1
            else "1girl, solo, hanfu, chinese clothes, holding sword"
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "ok": True,
                                    "prompt": prompt,
                                    "character_prompts": {},
                                    "error": None,
                                }
                            )
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_path = _write_tag_cache(
        tmp_path / "tags.sqlite3",
        {"1girl", "solo", "holding_sword", "hanfu", "chinese_clothes"},
    )
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    result = await planner.plan("古风大侠少女")
    await client.aclose()

    assert result["prompt"] == "1girl, solo, hanfu, chinese clothes, holding sword"
    assert deepseek_count == 2


@pytest.mark.asyncio
async def test_unreliable_tags_fail_after_bounded_repairs(tmp_path: Path) -> None:
    """Fail clearly instead of returning unverified tags after three attempts."""
    deepseek_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deepseek_count
        deepseek_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"ok":true,"prompt":"1girl, solo, '
                                'invented visual phrase",'
                                '"character_prompts":{},"error":null}'
                            )
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_path = _write_tag_cache(tmp_path / "tags.sqlite3", {"1girl", "solo"})
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    with pytest.raises(PlannerError, match="invented visual phrase"):
        await planner.plan("女孩")
    await client.aclose()

    assert deepseek_count == 3


@pytest.mark.asyncio
async def test_v4_character_action_prefix_validates_its_suffix(
    tmp_path: Path,
) -> None:
    """Allow NovelAI interaction syntax only in character prompts."""
    slot = "__NAI_CHARACTER_SLOT_1__"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "ok": True,
                                    "prompt": "1other, solo",
                                    "character_prompts": {
                                        slot: "other, source#pushing, standing"
                                    },
                                    "error": None,
                                }
                            )
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_path = _write_tag_cache(
        tmp_path / "tags.sqlite3",
        {"1other", "solo", "pushing", "standing"},
    )
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    result = await planner.plan(f"{slot}正在推人")
    await client.aclose()

    assert result["character_prompts"][slot].startswith("other, source#pushing")


@pytest.mark.asyncio
async def test_danbooru_validation_can_be_explicitly_disabled() -> None:
    """Keep an opt-out for private or offline deployments."""
    requested_hosts: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"ok":true,"prompt":"1girl, solo, '
                                'unverified phrase",'
                                '"character_prompts":{},"error":null}'
                            )
                        },
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client,
    )

    result = await planner.plan("女孩")
    await client.aclose()

    assert result["ok"] is True
    assert requested_hosts == ["api.deepseek.com"]


@pytest.mark.asyncio
async def test_missing_local_cache_fails_before_deepseek(tmp_path: Path) -> None:
    """Do not spend a model request when strict local data is unavailable."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("DeepSeek must not be called without a local tag cache")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(
        PlannerSettings(
            api_key="test-key",
            danbooru_cache_path=str(tmp_path / "missing.sqlite3"),
        ),
        client,
    )

    with pytest.raises(PlannerError, match="本地 Danbooru 词库"):
        await planner.plan("女孩")
    await client.aclose()


@pytest.mark.asyncio
async def test_snapshot_update_builds_a_local_alias_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download once, then resolve aliases exclusively from SQLite."""
    monkeypatch.setattr("nai_prompt_planner.tag_cache.MIN_SNAPSHOT_TAGS", 2)
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if (
            request.url.host == "huggingface.co"
            and "/api/datasets/" in request.url.path
        ):
            return httpx.Response(
                200,
                json=[
                    {"path": "danbooru-2026-07-21.csv"},
                    {"path": "danbooru-2026-07-22.csv"},
                    {"path": "README.md"},
                ],
            )
        return httpx.Response(
            200,
            content=(
                b'1girl,0,8178124,"sole_female,1girls"\nchibi,0,366374,super_deformed\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_path = tmp_path / "tags.sqlite3"

    info = await update_danbooru_cache(cache_path, client)
    resolved, metadata = lookup_local_tags(
        {"sole_female", "super_deformed"},
        cache_path,
    )
    await client.aclose()

    assert info.snapshot_date == "2026-07-22"
    assert info.tag_count == 2
    assert resolved == {"sole_female": "1girl", "super_deformed": "chibi"}
    assert metadata["1girl"].post_count == 8_178_124
    assert len(requests) == 2


def test_bundled_prompts_track_the_plugin_skill() -> None:
    """Detect drift between the extracted resources and the plugin runtime skill."""
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    plugin_root = project_root.parent
    source_root = plugin_root / "skills" / "novelai-prompt-planner" / "references"
    bundled_root = project_root / "src" / "nai_prompt_planner" / "prompts"
    for filename in (
        "runtime-system-prompt.txt",
        "runtime-semantic-expansion.txt",
    ):
        source = (source_root / filename).read_text(encoding="utf-8").strip()
        bundled = (bundled_root / filename).read_text(encoding="utf-8").strip()
        assert bundled.replace("调用方", "插件") == source
