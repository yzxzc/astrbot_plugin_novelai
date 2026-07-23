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
    assert "中文、日文、英文或混合语言" in system_prompt
    assert "主导意象" in system_prompt
    assert "一个主景别或视角" in system_prompt
    assert "普通人物展示、纯心理状态、Q版" in system_prompt
    assert "不得用 `1other` 代替未知性别" in system_prompt


def test_poetic_underwater_scene_rejects_semantic_collapse() -> None:
    """Reject emotion-only output that drops a depictable Japanese metaphor."""
    description = "悲しみの海に沈んだ私\n目を開けるのも億劫"
    collapsed = {
        "ok": True,
        "prompt": (
            "1other, solo, sad, depressed, closed eyes, lying, "
            "hand on face, simple background"
        ),
        "character_prompts": {},
        "error": None,
    }
    repaired = {
        "ok": True,
        "prompt": (
            "solo, underwater, ocean, submerged, sinking, floating, "
            "closed eyes, exhausted, depressed, expressionless, floating hair, "
            "floating clothes, outstretched arms, blue theme, wide shot, "
            "from above, negative space, darkness, light rays"
        ),
        "character_prompts": {},
        "error": None,
    }

    assert DeepSeekPromptPlanner._semantic_plan_errors(description, collapsed) == [
        "缺少 exhausted",
        "缺少 underwater/sinking 水下动作",
        "缺少 ocean/sea 水下环境",
        "水下意象不能使用 simple background",
        "不要用 1other 代替未知性别",
    ]
    assert DeepSeekPromptPlanner._semantic_plan_errors(description, repaired) == []


@pytest.mark.asyncio
async def test_poetic_underwater_scene_is_repaired_before_return() -> None:
    """Retry when a valid JSON response ignores the source language and image."""
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        prompt = (
            "1other, solo, sad, depressed, closed eyes, simple background"
            if request_count == 1
            else (
                "solo, underwater, ocean, submerged, sinking, floating, "
                "closed eyes, exhausted, depressed, expressionless, "
                "floating hair, blue theme, wide shot, from above, "
                "negative space, darkness, light rays"
            )
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
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client=client,
    )

    result = await planner.plan("悲しみの海に沈んだ私\n目を開けるのも億劫")
    await client.aclose()

    assert result["prompt"].startswith("solo, underwater, ocean")
    assert "wide shot" in result["prompt"]
    assert "negative space" in result["prompt"]
    assert request_count == 2


def test_visible_posture_and_ashes_are_not_discarded_as_inner_state() -> None:
    """Require explicit body and material cues without inventing another scene."""
    internal_errors = DeepSeekPromptPlanner._semantic_plan_errors(
        "何もしたくない、ただ丸くなってぼんやりしたい",
        {
            "ok": True,
            "prompt": "solo, simple background",
            "character_prompts": {},
            "error": None,
        },
    )
    ashes_errors = DeepSeekPromptPlanner._semantic_plan_errors(
        "燃尽后只剩一具空壳，呆坐在灰烬里",
        {
            "ok": True,
            "prompt": "solo, sitting, exhausted, simple background",
            "character_prompts": {},
            "error": None,
        },
    )

    assert internal_errors == ["缺少 curled posture"]
    assert ashes_errors == ["缺少 ashes"]


@pytest.mark.parametrize(
    ("description", "prompt"),
    [
        (
            "海に沈む夕日を見つめる少女",
            "1girl, solo, ocean, sunset, looking at horizon",
        ),
        ("沉静如水的少年，坐在窗边", "1boy, solo, sitting, window"),
        (
            "No underwater; ocean-side portrait of a girl.",
            "1girl, solo, ocean, portrait",
        ),
        (
            "我坐在干燥的卧室里，像溺水般难受，但画面不要海、水或水下元素",
            "solo, sitting, bedroom, dry",
        ),
        ("我看着夕阳沉入海面", "solo, sunset, ocean, looking afar"),
        (
            "少女は夕日が海に沈むのを見つめる",
            "1girl, solo, sunset, ocean, looking afar",
        ),
        (
            "A girl watches the sun sink into the ocean.",
            "1girl, solo, sunset, ocean, looking afar",
        ),
    ],
)
def test_underwater_guard_does_not_reverse_subject_or_negation(
    description: str,
    prompt: str,
) -> None:
    """Do not force a person underwater when another noun sinks or water is banned."""
    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            description,
            {
                "ok": True,
                "prompt": prompt,
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "description",
    [
        "No smile: a girl is underwater in the ocean.",
        "Without shoes, a girl sinks underwater in the ocean.",
        "不要鞋子的女孩沉入海水",
    ],
)
def test_unrelated_negation_does_not_disable_underwater_subject(
    description: str,
) -> None:
    """Bind negation to water terms instead of any earlier source word."""
    errors = DeepSeekPromptPlanner._semantic_plan_errors(
        description,
        {
            "ok": True,
            "prompt": "1girl, solo, expressionless, simple background",
            "character_prompts": {},
            "error": None,
        },
    )

    assert errors == [
        "缺少 underwater/sinking 水下动作",
        "缺少 ocean/sea 水下环境",
        "水下意象不能使用 simple background",
    ]


@pytest.mark.parametrize(
    "description",
    [
        "One gender-neutral person stands alone.",
        "An agender traveler stands alone.",
        "ジェンダーニュートラルな人物が一人で立っている",
        "ノンバイナリの旅人が一人で立っている",
        "Xジェンダーの人物が一人で立っている",
        "一个中性人物独自站立",
        "一个非二元旅行者独自站立",
    ],
)
def test_explicit_neutral_identity_allows_one_other(description: str) -> None:
    """Permit 1other only when the source binds neutrality to the subject."""
    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            description,
            {
                "ok": True,
                "prompt": "1other, solo, standing",
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "description",
    [
        "一个女孩，穿中性色西装",
        "可爱的__NAI_CHARACTER_SLOT_1__",
    ],
)
def test_one_other_cannot_hide_behind_clothing_or_character_slots(
    description: str,
) -> None:
    """Reject a guessed main subject even when neutral words or slots are present."""
    assert DeepSeekPromptPlanner._semantic_plan_errors(
        description,
        {
            "ok": True,
            "prompt": "1other, solo, suit",
            "character_prompts": {},
            "error": None,
        },
    ) == ["不要用 1other 代替未知性别"]


def test_japanese_people_relationship_and_season_keep_their_anchors() -> None:
    """Give Japanese descriptions the same deterministic core coverage as Chinese."""
    errors = DeepSeekPromptPlanner._semantic_plan_errors(
        "二人の女の子が春に抱き合う",
        {
            "ok": True,
            "prompt": "1girl, solo, standing, simple background",
            "character_prompts": {},
            "error": None,
        },
    )

    assert errors == ["缺少 2girls", "缺少 hugging", "缺少 spring"]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("孤独是一座铁笼，我蜷缩在栏杆后的阴影里", ["缺少 cage"]),
        ("My heart is encased in ice.", ["缺少 ice"]),
    ],
)
def test_visible_material_metaphors_keep_one_exact_anchor(
    description: str,
    expected: list[str],
) -> None:
    """Keep explicit visible materials without prescribing a full composition."""
    errors = DeepSeekPromptPlanner._semantic_plan_errors(
        description,
        {
            "ok": True,
            "prompt": "solo, curled up, expressionless, simple background",
            "character_prompts": {},
            "error": None,
        },
    )

    assert errors == expected


@pytest.mark.parametrize(
    ("description", "prompt"),
    [
        ("Nicolas Cage smiles at the viewer.", "1boy, solo, smile"),
        ("A girl with a frozen smile.", "1girl, solo, frozen smile"),
        ("They break the ice with a joke.", "2others, laughing"),
        ("A girl with an ice-cold gaze.", "1girl, solo, expressionless"),
    ],
)
def test_material_guards_ignore_names_and_idioms(
    description: str,
    prompt: str,
) -> None:
    """Require physical materials only for high-confidence visual phrasing."""
    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            description,
            {
                "ok": True,
                "prompt": prompt,
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "description",
    ["一个人困在厚厚的冰层下", "氷に閉じ込められた人物"],
)
def test_clear_physical_ice_scene_keeps_its_material(description: str) -> None:
    """Reject emotion-only output when a person is physically trapped in ice."""
    assert DeepSeekPromptPlanner._semantic_plan_errors(
        description,
        {
            "ok": True,
            "prompt": "solo, sad, simple background",
            "character_prompts": {},
            "error": None,
        },
    ) == ["缺少 ice"]


def test_japanese_negations_and_place_name_do_not_reverse_constraints() -> None:
    """Respect Japanese negation and avoid treating Kasukabe as spring."""
    no_hug = "二人の女の子は春に抱き合わない"
    not_two = "二人の女の子ではなく、一人の少女が立っている"
    kasukabe = "春日部で二人の女の子が立っている"

    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            no_hug,
            {
                "ok": True,
                "prompt": "2girls, spring (season), standing",
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )
    assert DeepSeekPromptPlanner._semantic_plan_errors(
        no_hug,
        {
            "ok": True,
            "prompt": "2girls, spring (season), hugging",
            "character_prompts": {},
            "error": None,
        },
    ) == ["错误增加 hugging"]
    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            not_two,
            {
                "ok": True,
                "prompt": "1girl, solo, standing",
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )
    assert (
        DeepSeekPromptPlanner._semantic_plan_errors(
            kasukabe,
            {
                "ok": True,
                "prompt": "2girls, standing",
                "character_prompts": {},
                "error": None,
            },
        )
        == []
    )


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


def test_parse_success_tolerates_omitted_null_error() -> None:
    """Normalize the harmless JSON-mode omission DeepSeek Flash returns live."""
    result = parse_planner_response(
        '{"ok":true,"prompt":"1girl, solo","character_prompts":{}}',
        4000,
    )

    assert result == {
        "ok": True,
        "prompt": "1girl, solo",
        "character_prompts": {},
        "error": None,
    }


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
async def test_long_mixed_tag_prompt_bypasses_deepseek() -> None:
    """Return a dense mixed tag list without requiring API or cache access."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Direct prompt must not call DeepSeek")

    prompt = (
        "零零零, chen bin, white hair, cat tail, white t–shirt, "
        "heterochromia, red eye, blue eye, looking at viewer, gradient hair"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = DeepSeekPromptPlanner(PlannerSettings(api_key=""), client)

    result = await planner.plan(prompt)
    await client.aclose()

    assert result == {
        "ok": True,
        "prompt": prompt,
        "character_prompts": {},
        "error": None,
    }


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
async def test_common_readable_phrases_normalize_to_exact_tags() -> None:
    """Repair a small observed phrase set without weakening strict validation."""

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
                                    "prompt": (
                                        "solo, hugging oneself, hugging self, holding oneself, "
                                        "self hugging, ash, burnt remains, dark atmosphere, "
                                        "setting sun, sunset reflection"
                                    ),
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
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client,
    )

    result = await planner.plan("A quiet figure contemplating burnt remains")
    await client.aclose()

    assert result["prompt"] == (
        "solo, self hug, ashes, burnt, debris, dark background, sunset, reflection"
    )


@pytest.mark.asyncio
async def test_unreliable_optional_tags_use_best_candidate_after_bounded_repairs(
    tmp_path: Path,
) -> None:
    """Keep the highest-hit candidate instead of deleting its remaining phrase."""
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

    result = await planner.plan("女孩")
    await client.aclose()

    assert result["prompt"] == "1girl, solo, invented visual phrase"
    assert deepseek_count == 3


@pytest.mark.asyncio
async def test_highest_hit_candidate_wins_after_three_failed_repairs(
    tmp_path: Path,
) -> None:
    """Return the candidate with the strongest local vocabulary hit rate."""
    deepseek_count = 0
    prompts = (
        "1girl, solo, rooftop, invented one, invented two, invented three",
        (
            "1girl, solo, rooftop, scenery, juice box, "
            "invented one, invented two, invented three"
        ),
        "1girl, solo, invented one, invented two, invented three",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal deepseek_count
        prompt = prompts[deepseek_count]
        deepseek_count += 1
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
        {"1girl", "solo", "rooftop", "scenery", "juice_box"},
    )
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    result = await planner.plan("女孩")
    await client.aclose()

    assert result["prompt"] == prompts[1]
    assert deepseek_count == 3


@pytest.mark.asyncio
async def test_invalid_character_interaction_never_uses_hit_rate_fallback(
    tmp_path: Path,
) -> None:
    """Keep an invalid native V4 interaction as a hard planner failure."""
    deepseek_count = 0
    slot = "__NAI_CHARACTER_SLOT_1__"

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
                            "content": json.dumps(
                                {
                                    "ok": True,
                                    "prompt": "1girl, solo",
                                    "character_prompts": {
                                        slot: "girl, source#invented action"
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
    cache_path = _write_tag_cache(tmp_path / "tags.sqlite3", {"1girl", "solo"})
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    with pytest.raises(PlannerError, match="source#invented action"):
        await planner.plan(f"女孩{slot}")
    await client.aclose()

    assert deepseek_count == 3


@pytest.mark.asyncio
async def test_unreliable_tags_receive_targeted_candidate_repair(
    tmp_path: Path,
) -> None:
    """Preserve a valid composition while asking DeepSeek to replace one bad tag."""
    requested_prompts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_prompts.append(payload["messages"][1]["content"])
        prompt = (
            "1girl, solo, invented visual phrase"
            if len(requested_prompts) == 1
            else "1girl, solo"
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
    cache_path = _write_tag_cache(tmp_path / "tags.sqlite3", {"1girl", "solo"})
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", danbooru_cache_path=str(cache_path)),
        client,
    )

    result = await planner.plan("女孩")
    await client.aclose()

    assert result["prompt"] == "1girl, solo"
    assert len(requested_prompts) == 2
    assert "上一版候选 JSON" in requested_prompts[1]
    assert "invented visual phrase" in requested_prompts[1]
    assert "不要重新设计整幅画" in requested_prompts[1]


@pytest.mark.asyncio
async def test_common_replacements_cover_character_prompts() -> None:
    """Normalize observed gaze and packaged-drink phrases in both prompt areas."""
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
                                    "prompt": "solo, rooftop, juice pack",
                                    "character_prompts": {
                                        slot: "holding juice box, looking at distance"
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
    planner = DeepSeekPromptPlanner(
        PlannerSettings(api_key="test-key", validate_danbooru_tags=False),
        client,
    )

    result = await planner.plan(f"在天台喝包装饮料眺望风景的学生{slot}")
    await client.aclose()

    assert result["prompt"] == "solo, rooftop, juice box"
    assert result["character_prompts"][slot] == (
        "holding drink, juice box, looking afar"
    )


@pytest.mark.asyncio
async def test_final_cleanup_cannot_remove_core_distant_gaze(tmp_path: Path) -> None:
    """Keep a source-requested gaze protected when the last repair is invalid."""
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
                        "message": {
                            "content": (
                                '{"ok":true,"prompt":"1girl, solo, '
                                'looking toward skyline",'
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

    with pytest.raises(PlannerError, match="缺少 looking afar"):
        await planner.plan("女孩眺望远方")
    await client.aclose()

    assert request_count == 3


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
                                    "prompt": "solo",
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
        {"solo", "pushing", "standing"},
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
