"""Regression tests for NovelAI generation routing and replies."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "main.py"
SPEC = importlib.util.spec_from_file_location("novelai_plugin_under_test", PLUGIN_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeEvent:
    """Return inspectable results without constructing an AstrBot event."""

    @staticmethod
    def plain_result(text: str) -> tuple[str, str]:
        """Build a fake plain-text result.

        Args:
            text: Reply text.

        Returns:
            Result kind and text.
        """
        return "plain", text

    @staticmethod
    def image_result(path: str) -> tuple[str, str]:
        """Build a fake image result.

        Args:
            path: Generated image path.

        Returns:
            Result kind and path.
        """
        return "image", path


class CharacterEvent:
    """Identify one group and sender for persistent character tests."""

    def __init__(self, sender_id: str = "10001", group_id: str = "20001") -> None:
        """Initialize stable test identifiers.

        Args:
            sender_id: QQ user identifier.
            group_id: QQ group identifier.
        """
        self.sender_id = sender_id
        self.group_id = group_id

    def get_sender_id(self) -> str:
        """Return the configured sender identifier."""
        return self.sender_id

    def get_group_id(self) -> str:
        """Return the configured group identifier."""
        return self.group_id

    @staticmethod
    def is_private_chat() -> bool:
        """Treat the test event as a group message."""
        return False


def build_plugin(
    planned_prompt: str = "planned prompt",
) -> MODULE.NovelAIWebPlugin:
    """Build a minimal plugin instance for command-level tests.

    Args:
        planned_prompt: Value returned by the mocked planner.

    Returns:
        Plugin with generation dependencies mocked.
    """
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {"max_prompt_length": 4000}
    plugin._generation_semaphore = asyncio.Semaphore(1)
    plugin._check_access = Mock()
    plugin._active_artist_string = AsyncMock(return_value=None)
    plugin._resolve_character_slots = AsyncMock(
        side_effect=lambda _event, prompt: (prompt, []),
    )
    plugin._user_generation_size = AsyncMock(return_value=(832, 1216))
    plugin._user_negative_prompt = AsyncMock(return_value="")
    plugin._join_generation_queue = AsyncMock(return_value=2)
    plugin._leave_generation_queue = AsyncMock()
    plugin._plan_prompt = AsyncMock(
        return_value={"prompt": planned_prompt, "character_prompts": {}},
    )
    plugin._restore_character_slots = Mock(side_effect=lambda prompt, _items: prompt)
    plugin._generate_from_api = AsyncMock(return_value=Path("generated.png"))
    plugin._remember_last_prompt = AsyncMock()
    return plugin


@pytest.mark.asyncio
async def test_tag_prompt_bypasses_planner_and_success_only_returns_image() -> None:
    """Keep a complete NovelAI tag prompt byte-for-byte unchanged."""
    plugin = build_plugin()
    prompt = "((artist:ame_usari)), [artist:sousouman], 1girl, solo"

    results = [
        result async for result in plugin.generate_image(FakeEvent(), f"生成 {prompt}")
    ]

    plugin._plan_prompt.assert_not_awaited()
    plugin._generate_from_api.assert_awaited_once_with(
        prompt,
        (832, 1216),
        (),
        "",
        (),
    )
    assert results == [("image", "generated.png")]


@pytest.mark.asyncio
async def test_natural_language_still_uses_planner() -> None:
    """Continue expanding concise natural-language scene requests."""
    plugin = build_plugin("1girl, eating ice cream, happy")

    results = [
        result
        async for result in plugin.generate_image(
            FakeEvent(),
            "生成 一个正在吃冰淇淋的可爱女孩",
        )
    ]

    plugin._plan_prompt.assert_awaited_once()
    plugin._generate_from_api.assert_awaited_once_with(
        "1girl, eating ice cream, happy",
        (832, 1216),
        (),
        "",
        (),
    )
    assert results == [("image", "generated.png")]


@pytest.mark.asyncio
async def test_api_failure_only_returns_error() -> None:
    """Return one explicit error and no image when API generation fails."""
    plugin = build_plugin()
    plugin._generate_from_api.side_effect = MODULE.NovelAIWebError("API unavailable")

    results = [
        result
        async for result in plugin.generate_image(FakeEvent(), "生成 1girl, solo")
    ]

    assert results == [("plain", "生成失败：API unavailable")]


def test_two_girl_spring_hug_plan_passes_semantic_validation() -> None:
    """Accept the exact base semantics requested by the user."""
    raw_response = (
        '{"ok":true,"prompt":"2girls, hugging, outdoors, spring, cherry '
        'blossoms, warm sunlight","character_prompts":{},"error":null}'
    )

    plan = MODULE.NovelAIWebPlugin._parse_planner_response(raw_response, 4000)

    assert (
        MODULE.NovelAIWebPlugin._semantic_plan_errors(
            "A和B两个女孩子在春光下抱在一起",
            plan,
        )
        == []
    )


def test_semantic_validation_rejects_painter_hallucination() -> None:
    """Reject the previously observed painter hallucination and omissions."""
    plan = {
        "prompt": "painter, drawing (action), holding paintbrush, canvas (object)",
        "character_prompts": {},
    }

    errors = MODULE.NovelAIWebPlugin._semantic_plan_errors(
        "A和B两个女孩子在春光下抱在一起",
        plan,
    )

    assert errors == [
        "缺少 2girls",
        "缺少 hugging",
        "缺少 spring",
        "凭空增加画师或画具",
    ]


def test_semantic_validation_respects_negated_hug() -> None:
    """Do not turn a negative interaction constraint into a required action."""
    valid_plan = {
        "prompt": "2girls, facing each other, outdoors, spring",
        "character_prompts": {},
    }
    invalid_plan = {
        "prompt": "2girls, facing each other, hugging, outdoors, spring",
        "character_prompts": {},
    }
    description = "两个女孩面对面，但不要拥抱"

    assert (
        MODULE.NovelAIWebPlugin._semantic_plan_errors(
            description,
            valid_plan,
        )
        == []
    )
    assert MODULE.NovelAIWebPlugin._semantic_plan_errors(
        description,
        invalid_plan,
    ) == ["错误增加 hugging"]


def test_semantic_validation_requires_complete_push_down_roles() -> None:
    """Require paired action roles and visible vertical posture for a push-down."""
    description = "__NAI_CHARACTER_SLOT_1__把__NAI_CHARACTER_SLOT_2__推倒"
    valid_plan = {
        "prompt": "2people, one person pushing another down, falling backward",
        "character_prompts": {
            "__NAI_CHARACTER_SLOT_1__": (
                "source#push, standing, leaning forward, looking down"
            ),
            "__NAI_CHARACTER_SLOT_2__": (
                "target#push, falling backward, lying on ground, looking up"
            ),
        },
    }
    invalid_plan = {
        "prompt": "2people, pushing, dynamic pose",
        "character_prompts": {
            "__NAI_CHARACTER_SLOT_1__": "source#push",
            "__NAI_CHARACTER_SLOT_2__": "target#falling",
        },
    }

    assert (
        MODULE.NovelAIWebPlugin._semantic_plan_errors(
            description,
            valid_plan,
        )
        == []
    )
    assert MODULE.NovelAIWebPlugin._semantic_plan_errors(
        description,
        invalid_plan,
    ) == [
        "缺少 push-down 动作结果",
        "被动人物缺少 target#push",
        "主动人物缺少推人姿态",
    ]


def test_native_character_prompts_preserve_identity_and_add_actions() -> None:
    """Keep saved identities separate while applying per-image interactions."""
    replacements = [
        (
            "__NAI_CHARACTER_SLOT_1__",
            "阿红",
            "1girl, solo, red hair, blue eyes",
            "",
        ),
        (
            "__NAI_CHARACTER_SLOT_2__",
            "阿蓝",
            "girl, blue hair, green eyes",
            "",
        ),
    ]
    dynamic_prompts = {
        "__NAI_CHARACTER_SLOT_1__": "girl, mutual#hug, happy",
        "__NAI_CHARACTER_SLOT_2__": "girl, mutual#hug, happy",
    }

    character_prompts = MODULE.NovelAIWebPlugin._build_character_prompts(
        replacements,
        dynamic_prompts,
        4000,
    )

    assert character_prompts == (
        "girl, red hair, blue eyes, mutual#hug, happy",
        "girl, blue hair, green eyes, mutual#hug, happy",
    )


def test_character_subject_counts_come_from_saved_prompts() -> None:
    """Replace planner-guessed counts with protected library subject types."""
    base_prompt = "2people, hugging, outdoors, spring"
    character_prompts = (
        "girl, red hair, mutual#hug",
        "boy, blue hair, mutual#hug",
    )

    result = MODULE.NovelAIWebPlugin._apply_character_subject_counts(
        base_prompt,
        character_prompts,
    )

    assert result == "1girl, 1boy, hugging, outdoors, spring"


@pytest.mark.asyncio
async def test_character_generation_uses_native_captions() -> None:
    """Route matched library characters into native V4 captions."""
    plugin = build_plugin()
    replacements = [
        (
            "__NAI_CHARACTER_SLOT_1__",
            "阿红",
            "girl, red hair, blue eyes",
            "extra fingers",
        ),
        (
            "__NAI_CHARACTER_SLOT_2__",
            "阿蓝",
            "girl, blue hair, green eyes",
            "bad eyes",
        ),
    ]
    plugin._resolve_character_slots = AsyncMock(
        return_value=(
            "__NAI_CHARACTER_SLOT_1__和__NAI_CHARACTER_SLOT_2__在春光下抱在一起",
            replacements,
        )
    )
    plugin._plan_prompt = AsyncMock(
        return_value={
            "prompt": "2girls, hugging, outdoors, spring",
            "character_prompts": {
                "__NAI_CHARACTER_SLOT_1__": "girl, mutual#hug",
                "__NAI_CHARACTER_SLOT_2__": "girl, mutual#hug",
            },
        }
    )

    results = [
        result
        async for result in plugin.generate_image(
            FakeEvent(),
            "生成 阿红和阿蓝在春光下抱在一起",
        )
    ]

    plugin._generate_from_api.assert_awaited_once_with(
        "2girls, hugging, outdoors, spring",
        (832, 1216),
        (
            "girl, red hair, blue eyes, mutual#hug",
            "girl, blue hair, green eyes, mutual#hug",
        ),
        "",
        ("extra fingers", "bad eyes"),
    )
    assert results == [("image", "generated.png")]


@pytest.mark.asyncio
async def test_redraw_reuses_native_character_captions() -> None:
    """Keep both base and character prompts unchanged when redrawing."""
    plugin = build_plugin()
    character_prompts = (
        "girl, red hair, mutual#hug",
        "girl, blue hair, mutual#hug",
    )
    plugin._last_successful_prompt = AsyncMock(
        return_value=(
            "2girls, hugging, spring",
            character_prompts,
            "lowres",
            ("extra fingers", "bad eyes"),
        ),
    )
    event = FakeEvent()

    results = [result async for result in plugin.generate_image(event, "重抽")]

    plugin._generate_from_api.assert_awaited_once_with(
        "2girls, hugging, spring",
        (832, 1216),
        character_prompts,
        "lowres",
        ("extra fingers", "bad eyes"),
    )
    plugin._remember_last_prompt.assert_awaited_once_with(
        event,
        "2girls, hugging, spring",
        character_prompts,
        "lowres",
        ("extra fingers", "bad eyes"),
    )
    assert results == [("image", "generated.png")]


@pytest.mark.asyncio
async def test_character_delete_requires_same_user_confirmation(tmp_path: Path) -> None:
    """Delete only after the requesting QQ confirms in the same group."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {"max_character_prompt_length": 2000}
    plugin._character_state_lock = asyncio.Lock()
    plugin._pending_character_changes = {}
    plugin._character_state_path = Mock(return_value=tmp_path / "characters.json")
    plugin._save_character_state(
        {
            "version": 1,
            "libraries": {"group:20001": {"prompts": {"撅撅": "cum, sex, steam, wet"}}},
        }
    )
    requester = CharacterEvent()
    other_user = CharacterEvent(sender_id="10002")

    staged_name = await plugin._stage_character_deletion(requester, "撅撅")

    assert staged_name == "撅撅"
    assert (
        "撅撅" in plugin._load_character_state()["libraries"]["group:20001"]["prompts"]
    )
    with pytest.raises(MODULE.NovelAIWebError, match="没有待确认"):
        await plugin._confirm_character_change(other_user)

    operation, deleted_name = await plugin._confirm_character_change(requester)

    assert (operation, deleted_name) == ("delete", "撅撅")
    assert plugin._load_character_state()["libraries"]["group:20001"]["prompts"] == {}


@pytest.mark.asyncio
async def test_character_delete_confirmation_expires(tmp_path: Path) -> None:
    """Keep a character when its deletion confirmation expires."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {"max_character_prompt_length": 2000}
    plugin._character_state_lock = asyncio.Lock()
    plugin._pending_character_changes = {}
    plugin._character_state_path = Mock(return_value=tmp_path / "characters.json")
    plugin._save_character_state(
        {
            "version": 1,
            "libraries": {"group:20001": {"prompts": {"撅撅": "cum, sex, steam, wet"}}},
        }
    )
    event = CharacterEvent()
    await plugin._stage_character_deletion(event, "撅撅")
    plugin._pending_character_changes[("group:20001", "10001")]["expires_at"] = (
        MODULE.monotonic() - 1
    )

    with pytest.raises(MODULE.NovelAIWebError, match="已超时"):
        await plugin._confirm_character_change(event)

    assert (
        "撅撅" in plugin._load_character_state()["libraries"]["group:20001"]["prompts"]
    )


@pytest.mark.asyncio
async def test_user_negative_prompt_is_scoped_by_user_and_group(
    tmp_path: Path,
) -> None:
    """Keep each QQ user's negative prompt isolated per conversation."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {}
    plugin._artist_state_lock = asyncio.Lock()
    plugin._artist_state_path = Mock(return_value=tmp_path / "artist_strings.json")
    first_group = CharacterEvent()
    second_group = CharacterEvent(group_id="20002")
    other_user = CharacterEvent(sender_id="10002")

    assert await plugin._user_negative_prompt(first_group) == ""
    assert (
        await plugin._user_negative_prompt(
            first_group,
            " lowres,  extra fingers, ",
        )
        == "lowres, extra fingers"
    )

    assert await plugin._user_negative_prompt(first_group) == "lowres, extra fingers"
    assert await plugin._user_negative_prompt(second_group) == ""
    assert await plugin._user_negative_prompt(other_user) == ""
    assert await plugin._user_negative_prompt(first_group, "") == ""


@pytest.mark.asyncio
async def test_character_negative_prompt_is_saved_and_resolved(tmp_path: Path) -> None:
    """Bind a shared character negative caption without changing its identity."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {"max_character_prompt_length": 2000}
    plugin._character_state_lock = asyncio.Lock()
    plugin._pending_character_changes = {}
    plugin._character_state_path = Mock(return_value=tmp_path / "characters.json")
    event = CharacterEvent()

    requires_confirmation = await plugin._add_character(
        event,
        "霜音",
        "1girl, silver hair, blue eyes",
        "extra fingers, bad hands",
    )
    slotted_description, replacements = await plugin._resolve_character_slots(
        event,
        "霜音正在吃冰淇淋",
    )

    assert requires_confirmation is False
    assert "__NAI_CHARACTER_SLOT_1__" in slotted_description
    assert replacements == [
        (
            "__NAI_CHARACTER_SLOT_1__",
            "霜音",
            "1girl, silver hair, blue eyes",
            "extra fingers, bad hands",
        )
    ]
    assert await plugin._character_text(event, "霜音") == (
        "人物「霜音」\n"
        "Prompt：1girl, silver hair, blue eyes\n"
        "负面：extra fingers, bad hands"
    )


@pytest.mark.asyncio
async def test_chibi_planning_keeps_hard_style_and_removes_realism() -> None:
    """Keep Q-version proportions ahead of ordinary semantic expansion."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {
        "prompt_planner_enabled": True,
        "prompt_planner_provider_id": "deepseek/deepseek-v4-flash",
    }
    response = Mock(
        completion_text=(
            '{"ok":true,"prompt":"1girl, cute, realistic proportions, '
            'photorealistic, eating ice cream, outdoors",'
            '"character_prompts":{},"error":null}'
        )
    )
    plugin.context = Mock()
    plugin.context.llm_generate = AsyncMock(return_value=response)

    plan = await plugin._plan_prompt("Q版女孩正在吃冰淇淋", 4000)

    assert plan["prompt"].startswith("chibi, super deformed, ")
    assert "realistic proportions" not in plan["prompt"]
    assert "photorealistic" not in plan["prompt"]
    system_prompt = plugin.context.llm_generate.await_args.kwargs["system_prompt"]
    assert "6–14 个紧凑标签" in system_prompt


@pytest.mark.asyncio
async def test_default_artist_and_explicit_original_are_distinct(
    tmp_path: Path,
) -> None:
    """Apply the global snapshot unless the user explicitly chooses original."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {
        "default_artist_string_name": "千代noob",
        "default_artist_string": "artist:test,",
    }
    plugin._artist_state_lock = asyncio.Lock()
    plugin._artist_state_path = Mock(return_value=tmp_path / "artist_strings.json")
    event = CharacterEvent()

    assert await plugin._active_artist_string(event) == ("千代noob", "artist:test")

    await plugin._switch_artist_string(event, "原生")
    assert await plugin._active_artist_string(event) is None

    await plugin._switch_artist_string(event, "默认")
    assert await plugin._active_artist_string(event) == ("千代noob", "artist:test")


@pytest.mark.asyncio
async def test_status_reports_queue_and_models_without_generation_lock() -> None:
    """Expose live local queue state while one request owns the semaphore."""
    plugin = MODULE.NovelAIWebPlugin.__new__(MODULE.NovelAIWebPlugin)
    plugin.config = {
        "steps": 23,
        "max_total_pixels": 1_048_576,
        "max_steps": 28,
        "prompt_planner_provider_id": "deepseek/deepseek-v4-flash",
    }
    plugin._check_access = Mock()
    plugin._user_generation_size = AsyncMock(return_value=(832, 1216))
    plugin._active_artist_string = AsyncMock(return_value=("千代noob", "artist:test"))
    plugin._user_negative_prompt = AsyncMock(return_value="")
    plugin._generation_queue_lock = asyncio.Lock()
    plugin._generation_queue_size = 3
    plugin._generation_semaphore = asyncio.Semaphore(0)
    plugin._read_subscription = AsyncMock(
        return_value={
            "active": True,
            "tier": 3,
            "trainingStepsLeft": {
                "fixedTrainingStepsLeft": 9000,
                "purchasedTrainingSteps": 0,
            },
        }
    )

    results = [result async for result in plugin.generation_status(FakeEvent())]

    assert len(results) == 1
    status = results[0][1]
    assert "队列: 生成中 1，等待 2，总计 3" in status
    assert "Prompt 模型: deepseek/deepseek-v4-flash" in status
    assert f"绘图模型: {MODULE.NOVELAI_MODEL}" in status
    assert "当前画风: 千代noob" in status
    plugin._read_subscription.assert_awaited_once()
