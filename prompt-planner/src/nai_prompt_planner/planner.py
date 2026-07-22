"""DeepSeek-backed NovelAI V4.5 prompt planning core."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TypedDict

import httpx

from .tag_cache import default_cache_path, lookup_local_tags, read_cache_info

DANBOORU_INTERACTION_PREFIX_PATTERN = re.compile(
    r"^(?:source|target|mutual)#",
    re.IGNORECASE,
)
DANBOORU_NUMERIC_WEIGHT_PATTERN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)::(.*)::$",
    re.DOTALL,
)
DANBOORU_NOVELAI_SPECIAL_TAGS = {
    "background_dataset",
    "fur_dataset",
    "location",
}
DANBOORU_NOVELAI_CHARACTER_TYPES = {"girl", "boy", "other"}
DANBOORU_NOVELAI_SPECIAL_PATTERNS = (re.compile(r"^year_\d{4}$"),)

CHARACTER_SLOT_PATTERN = re.compile(
    r"__NAI_CHARACTER_SLOT_\d+__",
    re.IGNORECASE,
)
CHIBI_SOURCE_PATTERN = re.compile(
    r"(?:Q版|Ｑ版|q版|chibi|super[\s_-]*deformed)",
    re.IGNORECASE,
)
PAINTER_SUBJECT_PATTERN = re.compile(
    r"(?:画师|画家(?!帽)|(?<![\w:])painter\b)",
    re.IGNORECASE,
)
PAINTER_STYLE_PATTERN = re.compile(
    r"(?:画师串|画师风格|画家风格|画风|artist\s*:|art\s+style|"
    r"in\s+the\s+style\s+of)",
    re.IGNORECASE,
)
PAINTER_NEGATION_PATTERN = re.compile(
    r"(?:不(?:要|在|是|拿|带)?(?:画画|绘画|作画|画笔|画具)|"
    r"没有(?:画具|画笔)|空手|下班|not\s+painting|"
    r"without\s+(?:art|paint)(?:ing)?\s+supplies|empty[- ]handed)",
    re.IGNORECASE,
)
PAINTER_VISUAL_ANCHOR_GROUPS = (
    (
        "drawing (action)",
        ("drawing (action)", "painting (action)"),
    ),
    (
        "holding paintbrush",
        ("holding paintbrush", "paintbrush", "holding brush"),
    ),
    ("canvas (object)", ("canvas", "canvas (object)")),
    ("easel", ("easel",)),
)
SEMANTIC_ANCHOR_RULES = (
    (
        "2girls",
        re.compile(
            r"(?:两个|两名|二个|2\s*个|2\s*名)\s*(?:女孩子|女孩|女生|少女)|"
            r"\b2\s*girls?\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:2girls|two girls)\b", re.IGNORECASE),
    ),
    (
        "hugging",
        re.compile(r"抱在一起|互相拥抱|相拥|拥抱|\bhugg?(?:ing|ed)?\b", re.I),
        re.compile(
            r"(?<![a-z])(?:mutual#|source#|target#)?hug(?:ging)?(?![a-z])|"
            r"\bembrac",
            re.IGNORECASE,
        ),
    ),
    (
        "spring",
        re.compile(r"春光|春日|春天|春季|\bspring\b", re.IGNORECASE),
        re.compile(r"\bspring\b", re.IGNORECASE),
    ),
    (
        "eating ice cream",
        re.compile(
            r"(?:吃|舔)\s*(?:着|了|一个)?\s*冰(?:激凌|淇淋)|ice cream",
            re.IGNORECASE,
        ),
        re.compile(r"ice cream", re.IGNORECASE),
    ),
    (
        "exhausted",
        re.compile(r"疲惫|疲倦|筋疲力尽|燃尽了|burned? out|exhausted", re.I),
        re.compile(r"exhausted|tired|fatigue|burned? out", re.IGNORECASE),
    ),
)
QUALITY_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9_])(?:masterpiece|best quality|very aesthetic|"
    r"absurdres|amazing quality|highres|score_\d+)(?![a-z0-9_])"
)
MANAGED_FIELD_PATTERN = re.compile(
    r"(?i)(?:\bartist\s*:|\bartist collaboration\b|\bchar\s*\d+\s*:|"
    r"\bundesired content\b)"
)
EXPLANATION_PREFIX_PATTERN = re.compile(
    r"(?i)^\s*(?:prompt|tags?|output|here\s+(?:is|are))\s*[:\-]"
)


class PlanResult(TypedDict):
    """Strict response returned by the standalone planner."""

    ok: bool
    prompt: str | None
    character_prompts: dict[str, str]
    error: str | None


class PlannerError(Exception):
    """Describe one safe planner failure without exposing credentials."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize a categorized planner error.

        Args:
            code: Stable machine-readable error code.
            message: Safe user-facing error message.
        """
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlannerSettings:
    """Environment-backed DeepSeek connection settings."""

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 60.0
    max_tokens: int = 2048
    thinking: str = "disabled"
    reasoning_effort: str = "high"
    json_mode: bool = True
    service_token: str = ""
    validate_danbooru_tags: bool = True
    danbooru_min_post_count: int = 50
    danbooru_cache_path: str = ""

    @classmethod
    def from_env(cls) -> PlannerSettings:
        """Load and validate service configuration from environment variables.

        Returns:
            Validated settings. The API key may be empty so `/health` can report
            an unconfigured service without crashing startup.

        Raises:
            PlannerError: If a configured numeric or enum value is invalid.
        """
        try:
            timeout_seconds = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "60"))
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "2048"))
            danbooru_min_post_count = int(
                os.environ.get("DANBOORU_MIN_POST_COUNT", "50")
            )
        except ValueError as exc:
            raise PlannerError(
                "invalid_config",
                "超时、Tokens 或 Danbooru 最低作品数配置无效。",
            ) from exc
        if not 1 <= timeout_seconds <= 600:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_TIMEOUT_SECONDS 必须在 1 到 600 之间。",
            )
        if not 128 <= max_tokens <= 32768:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_MAX_TOKENS 必须在 128 到 32768 之间。",
            )
        if not 50 <= danbooru_min_post_count <= 1_000_000:
            raise PlannerError(
                "invalid_config",
                "DANBOORU_MIN_POST_COUNT 必须在 50 到 1000000 之间。",
            )
        thinking = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower()
        if thinking not in {"disabled", "enabled", "omit"}:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_THINKING 必须是 disabled、enabled 或 omit。",
            )
        reasoning_effort = (
            os.environ.get("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()
        )
        if reasoning_effort not in {"high", "max"}:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_REASONING_EFFORT 必须是 high 或 max。",
            )
        json_mode_text = os.environ.get("DEEPSEEK_JSON_MODE", "true").strip().lower()
        if json_mode_text not in {"1", "0", "true", "false", "yes", "no"}:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_JSON_MODE 必须是 true 或 false。",
            )
        validate_danbooru_text = (
            os.environ.get("DANBOORU_VALIDATE_TAGS", "true").strip().lower()
        )
        if validate_danbooru_text not in {"1", "0", "true", "false", "yes", "no"}:
            raise PlannerError(
                "invalid_config",
                "DANBOORU_VALIDATE_TAGS 必须是 true 或 false。",
            )
        base_url = os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ).strip()
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
        if not base_url.startswith(("https://", "http://")) or not model:
            raise PlannerError(
                "invalid_config",
                "DEEPSEEK_BASE_URL 或 DEEPSEEK_MODEL 配置无效。",
            )
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            base_url=base_url.rstrip("/"),
            model=model,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            json_mode=json_mode_text in {"1", "true", "yes"},
            service_token=os.environ.get("PLANNER_SERVICE_TOKEN", "").strip(),
            validate_danbooru_tags=validate_danbooru_text in {"1", "true", "yes"},
            danbooru_min_post_count=danbooru_min_post_count,
            danbooru_cache_path=os.environ.get("DANBOORU_CACHE_PATH", "").strip(),
        )


def load_system_prompt() -> str:
    """Load the two bundled runtime prompt resources.

    Returns:
        Concatenated system and semantic expansion instructions.

    Raises:
        PlannerError: If a bundled prompt resource is missing or empty.
    """
    sections: list[str] = []
    try:
        prompt_root = resources.files("nai_prompt_planner").joinpath("prompts")
        for filename in (
            "runtime-system-prompt.txt",
            "runtime-semantic-expansion.txt",
        ):
            section = prompt_root.joinpath(filename).read_text(encoding="utf-8").strip()
            if not section:
                raise PlannerError(
                    "prompt_resource_error",
                    f"Prompt 资源为空：{filename}",
                )
            sections.append(section)
    except (FileNotFoundError, OSError) as exc:
        raise PlannerError(
            "prompt_resource_error",
            "无法读取内置 Prompt 规划资源。",
        ) from exc
    return "\n\n".join(sections)


def parse_planner_response(
    raw_response: str,
    max_length: int,
    required_character_slots: tuple[str, ...] = (),
) -> PlanResult:
    """Validate one strict JSON response returned by DeepSeek.

    Args:
        raw_response: Raw assistant message content.
        max_length: Maximum combined prompt character count.
        required_character_slots: Protected character keys found in the input.

    Returns:
        A validated success or conflicting-constraints response.

    Raises:
        PlannerError: If the response violates the machine protocol.
    """
    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PlannerError(
            "invalid_model_output", "DeepSeek 没有返回有效 JSON。"
        ) from exc
    expected_fields = {"ok", "prompt", "character_prompts", "error"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise PlannerError(
            "invalid_model_output", "DeepSeek 返回了协议外字段或缺少字段。"
        )
    if not isinstance(payload["ok"], bool):
        raise PlannerError("invalid_model_output", "DeepSeek 返回了无效 ok 字段。")
    if payload["ok"] is False:
        if (
            payload["prompt"] is not None
            or payload["character_prompts"] != {}
            or payload["error"] != "conflicting_constraints"
        ):
            raise PlannerError("invalid_model_output", "DeepSeek 返回了无效失败协议。")
        return {
            "ok": False,
            "prompt": None,
            "character_prompts": {},
            "error": "conflicting_constraints",
        }
    if payload["error"] is not None:
        raise PlannerError("invalid_model_output", "成功响应的 error 必须为 null。")

    planned_prompt = payload["prompt"]
    if not isinstance(planned_prompt, str) or not planned_prompt.strip(" ,"):
        raise PlannerError("invalid_model_output", "DeepSeek 没有返回有效 Prompt。")
    if any(ord(character) < 32 for character in planned_prompt):
        raise PlannerError("invalid_model_output", "Prompt 包含控制字符。")
    planned_prompt = re.sub(r" +", " ", planned_prompt).strip(" ,")
    if (
        not planned_prompt.isascii()
        or "```" in planned_prompt
        or EXPLANATION_PREFIX_PATTERN.search(planned_prompt)
    ):
        raise PlannerError(
            "invalid_model_output", "Prompt 必须只包含英文标签且不能包含 Markdown。"
        )
    if MANAGED_FIELD_PATTERN.search(planned_prompt):
        raise PlannerError("invalid_model_output", "Prompt 包含应由调用方管理的字段。")
    if QUALITY_PATTERN.search(planned_prompt):
        raise PlannerError(
            "invalid_model_output",
            "Prompt 包含由 NovelAI Quality Toggle 管理的质量词。",
        )
    if CHARACTER_SLOT_PATTERN.search(planned_prompt):
        raise PlannerError("invalid_model_output", "人物占位符不能出现在主 Prompt 中。")

    raw_character_prompts = payload["character_prompts"]
    expected_slots = set(required_character_slots)
    if (
        not isinstance(raw_character_prompts, dict)
        or set(raw_character_prompts) != expected_slots
    ):
        raise PlannerError(
            "invalid_model_output", "DeepSeek 改动或遗漏了人物 Prompt 键。"
        )
    character_prompts: dict[str, str] = {}
    for slot in required_character_slots:
        value = raw_character_prompts.get(slot)
        if not isinstance(value, str) or any(
            ord(character) < 32 for character in value
        ):
            raise PlannerError(
                "invalid_model_output", "DeepSeek 返回了无效人物 Prompt。"
            )
        value = re.sub(r" +", " ", value).strip(" ,")
        if not value.isascii() or "```" in value:
            raise PlannerError(
                "invalid_model_output", "人物 Prompt 必须只包含英文标签。"
            )
        if (
            CHARACTER_SLOT_PATTERN.search(value)
            or MANAGED_FIELD_PATTERN.search(value)
            or QUALITY_PATTERN.search(value)
        ):
            raise PlannerError(
                "invalid_model_output", "人物 Prompt 包含禁止字段或质量词。"
            )
        character_prompts[slot] = value

    if len(planned_prompt) + sum(map(len, character_prompts.values())) > max_length:
        raise PlannerError(
            "invalid_model_output",
            f"规划后的 Prompt 超过 {max_length} 个字符。",
        )
    return {
        "ok": True,
        "prompt": planned_prompt,
        "character_prompts": character_prompts,
        "error": None,
    }


class DeepSeekPromptPlanner:
    """Call DeepSeek and validate its NovelAI prompt plan."""

    def __init__(
        self,
        settings: PlannerSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize one reusable planner.

        Args:
            settings: Validated API and model settings.
            client: Optional injected client for tests or shared lifecycle use.
        """
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds)
        )

    async def aclose(self) -> None:
        """Close the internally owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def plan(self, description: str, max_length: int = 4000) -> PlanResult:
        """Plan one natural-language description with bounded repair attempts.

        Args:
            description: Natural-language scene description containing optional
                protected character slot tokens.
            max_length: Maximum combined main and dynamic prompt characters.

        Returns:
            Strict planner response suitable for a caller-side adapter.

        Raises:
            PlannerError: If configuration, network, or model validation fails.
        """
        description = description.strip()
        if not description:
            raise PlannerError("invalid_request", "description 不能为空。")
        if len(description) > 20_000 or not 1 <= max_length <= 20_000:
            raise PlannerError(
                "invalid_request", "description 或 max_length 超出允许范围。"
            )
        if not self.settings.api_key:
            raise PlannerError("missing_api_key", "未设置 DEEPSEEK_API_KEY。")
        if self.settings.validate_danbooru_tags:
            cache_path = (
                default_cache_path()
                if not self.settings.danbooru_cache_path
                else Path(self.settings.danbooru_cache_path)
            )
            if read_cache_info(cache_path) is None:
                raise PlannerError(
                    "danbooru_cache_missing",
                    "本地 Danbooru 词库不存在或已损坏，请先更新本地词库。",
                )
        required_slots = tuple(
            dict.fromkeys(CHARACTER_SLOT_PATTERN.findall(description))
        )
        system_prompt = load_system_prompt()
        if CHIBI_SOURCE_PATTERN.search(description):
            system_prompt += (
                "\n\n本次输入包含强风格约束 Q版/chibi。必须在主 Prompt 开头保留 "
                "`chibi`；身份、动作和必要场景仍需表达，但使用 "
                "6–14 个紧凑标签，避免自动补充 realistic proportions、photorealistic、"
                "tall、long legs 或写实电影镜头等会稀释 Q 版比例的内容。"
            )
        retry_prompt = description
        last_error: PlannerError | None = None

        for attempt in range(3):
            try:
                raw_response = await self._request_completion(
                    system_prompt, retry_prompt
                )
                result = parse_planner_response(
                    raw_response,
                    max_length,
                    required_slots,
                )
                if not result["ok"]:
                    return result
                result["prompt"] = self._enforce_occupation_anchors(
                    description,
                    result["prompt"] or "",
                    max_length
                    - sum(len(value) for value in result["character_prompts"].values()),
                )
                if CHIBI_SOURCE_PATTERN.search(description):
                    prompt_items = [
                        item.strip()
                        for item in (result["prompt"] or "").split(",")
                        if item.strip()
                    ]
                    prompt_items = [
                        item
                        for item in prompt_items
                        if item.casefold()
                        not in {
                            "chibi",
                            "super deformed",
                            "realistic proportions",
                            "photorealistic",
                        }
                    ]
                    result["prompt"] = ", ".join(("chibi", *prompt_items))
                semantic_errors = self._semantic_plan_errors(description, result)
                if semantic_errors:
                    raise PlannerError(
                        "invalid_model_output",
                        "Prompt 遗漏或曲解核心语义："
                        + "、".join(semantic_errors)
                        + "。",
                    )
                if (
                    len(result["prompt"] or "")
                    + sum(map(len, result["character_prompts"].values()))
                    > max_length
                ):
                    raise PlannerError(
                        "invalid_model_output", "后处理后的 Prompt 超过长度上限。"
                    )
                if self.settings.validate_danbooru_tags:
                    await self._validate_danbooru_result(result)
                return result
            except PlannerError as exc:
                last_error = exc
                if exc.code not in {
                    "invalid_model_output",
                    "invalid_upstream_response",
                }:
                    raise
                if attempt < 2:
                    retry_prompt = (
                        f"上一次输出无效：{exc} 请重新规划以下原始描述，"
                        "逐项保留人数、主体、动作、关系和环境，"
                        "只返回协议规定的一行 JSON：\n" + description
                    )
        raise last_error or PlannerError(
            "invalid_model_output", "DeepSeek Prompt 规划失败。"
        )

    async def _validate_danbooru_result(self, result: PlanResult) -> None:
        """Reject invented phrases by checking exact Danbooru tag metadata.

        NovelAI-specific weighting and V4 interaction prefixes are removed only
        for lookup. Active Danbooru aliases are resolved before exact metadata
        validation. The final NovelAI prompt is left unchanged.

        Args:
            result: Parsed main and character prompts to validate.

        Raises:
            PlannerError: If tags are unreliable or Danbooru cannot be checked.
        """
        candidates: dict[str, list[str]] = {}
        prefixed_main_tags: list[str] = []
        prompt_groups = [(result["prompt"] or "", False)]
        prompt_groups.extend(
            (prompt, True) for prompt in result["character_prompts"].values()
        )
        for prompt, is_character_prompt in prompt_groups:
            for raw_item in prompt.split(","):
                item = raw_item.strip()
                if not item:
                    continue
                lookup_value = item
                for _ in range(8):
                    previous = lookup_value
                    weight_match = DANBOORU_NUMERIC_WEIGHT_PATTERN.fullmatch(
                        lookup_value
                    )
                    if weight_match:
                        lookup_value = weight_match.group(1).strip()
                    elif (
                        len(lookup_value) >= 2
                        and lookup_value[0] in "{["
                        and lookup_value[-1] == ("}" if lookup_value[0] == "{" else "]")
                    ):
                        lookup_value = lookup_value[1:-1].strip()
                    if lookup_value == previous:
                        break
                interaction_match = DANBOORU_INTERACTION_PREFIX_PATTERN.match(
                    lookup_value
                )
                if interaction_match:
                    if not is_character_prompt:
                        prefixed_main_tags.append(item)
                    lookup_value = lookup_value[interaction_match.end() :].strip()
                normalized = re.sub(r"\s+", "_", lookup_value.casefold())
                if not normalized:
                    candidates.setdefault(normalized, []).append(item)
                    continue
                if normalized in DANBOORU_NOVELAI_SPECIAL_TAGS or any(
                    pattern.fullmatch(normalized)
                    for pattern in DANBOORU_NOVELAI_SPECIAL_PATTERNS
                ):
                    continue
                if (
                    is_character_prompt
                    and normalized in DANBOORU_NOVELAI_CHARACTER_TYPES
                ):
                    continue
                candidates.setdefault(normalized, []).append(item)

        if prefixed_main_tags:
            raise PlannerError(
                "invalid_model_output",
                "V4 source#/target#/mutual# 动作只能出现在人物 Prompt："
                + "、".join(prefixed_main_tags[:8]),
            )
        if not candidates:
            return
        if "" in candidates or len(candidates) > 200:
            raise PlannerError(
                "invalid_model_output",
                "Prompt 包含无法解析的标签语法或超过 200 个不同标签。",
            )

        cache_path = (
            default_cache_path()
            if not self.settings.danbooru_cache_path
            else Path(self.settings.danbooru_cache_path)
        )
        try:
            resolved, metadata = lookup_local_tags(set(candidates), cache_path)
        except (FileNotFoundError, OSError, sqlite3.Error) as exc:
            raise PlannerError(
                "danbooru_cache_error",
                "无法读取本地 Danbooru 词库，请重新更新词库。",
            ) from exc
        invalid_items: list[str] = []
        for original, resolved_name in resolved.items():
            tag = metadata.get(resolved_name)
            valid = (
                tag is not None
                and tag.post_count >= self.settings.danbooru_min_post_count
                and tag.category != 1
            )
            if not valid:
                invalid_items.extend(candidates[original])
        if invalid_items:
            shown = "、".join(dict.fromkeys(invalid_items[:20]))
            suffix = "等" if len(dict.fromkeys(invalid_items)) > 20 else ""
            raise PlannerError(
                "invalid_model_output",
                "以下内容不在本地可靠 Danbooru 词库中（不存在、作品数过低或为画师标签）："
                f"{shown}{suffix}。请只用现行精确 tag 替换，不要改写原始需求。",
            )

    async def _request_completion(self, system_prompt: str, user_prompt: str) -> str:
        """Send one OpenAI-compatible DeepSeek chat completion request.

        Args:
            system_prompt: Complete planner system instructions.
            user_prompt: Original description or one repair request.

        Returns:
            Assistant message content.

        Raises:
            PlannerError: If the upstream request or response fails.
        """
        endpoint = self.settings.base_url
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        request_body: dict[str, object] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        if self.settings.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        if self.settings.thinking != "omit":
            request_body["thinking"] = {"type": self.settings.thinking}
            if self.settings.thinking == "enabled":
                request_body["reasoning_effort"] = self.settings.reasoning_effort
        try:
            response = await self._client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
        except httpx.TimeoutException as exc:
            raise PlannerError("deepseek_timeout", "DeepSeek API 请求超时。") from exc
        except httpx.HTTPError as exc:
            raise PlannerError("deepseek_network", "无法连接 DeepSeek API。") from exc
        if response.status_code in {401, 403}:
            raise PlannerError("deepseek_auth", "DeepSeek API Key 无效或没有模型权限。")
        if response.status_code == 429:
            raise PlannerError("deepseek_rate_limit", "DeepSeek API 当前限流。")
        if response.status_code >= 400:
            raise PlannerError(
                "deepseek_http_error",
                f"DeepSeek API 返回 HTTP {response.status_code}。",
            )
        try:
            payload = response.json()
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise PlannerError(
                    "invalid_model_output", "DeepSeek 输出达到长度上限。"
                )
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise PlannerError(
                "invalid_upstream_response", "DeepSeek API 响应结构无效。"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise PlannerError("invalid_model_output", "DeepSeek 返回了空内容。")
        return content.strip()

    @staticmethod
    def _enforce_occupation_anchors(
        description: str,
        planned_prompt: str,
        max_length: int,
    ) -> str:
        """Keep visually explicit painter anchors after LLM compression.

        Args:
            description: Original user description.
            planned_prompt: Validated main prompt.
            max_length: Remaining main prompt character budget.

        Returns:
            Main prompt containing all required painter anchors.

        Raises:
            PlannerError: If deterministic anchors exceed the length budget.
        """
        if (
            not PAINTER_SUBJECT_PATTERN.search(description)
            or PAINTER_STYLE_PATTERN.search(description)
            or PAINTER_NEGATION_PATTERN.search(description)
        ):
            return planned_prompt
        prompt_items = [
            item.strip() for item in planned_prompt.split(",") if item.strip()
        ]
        normalized_items = [
            re.sub(r"\s+", " ", item.casefold()) for item in prompt_items
        ]

        def has_alias(alias: str) -> bool:
            alias_pattern = re.compile(
                rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])",
                re.IGNORECASE,
            )
            return any(alias_pattern.search(item) for item in normalized_items)

        missing = [
            canonical
            for canonical, aliases in PAINTER_VISUAL_ANCHOR_GROUPS
            if not any(has_alias(alias) for alias in aliases)
        ]
        if not missing:
            return planned_prompt
        expanded_prompt = ", ".join((*prompt_items, *missing))
        if len(expanded_prompt) > max_length:
            raise PlannerError(
                "invalid_model_output", "补全职业视觉锚点后超过 Prompt 长度上限。"
            )
        return expanded_prompt

    @staticmethod
    def _semantic_plan_errors(
        description: str,
        result: PlanResult,
    ) -> list[str]:
        """Find deterministic omissions or painter hallucinations.

        Args:
            description: Original user description.
            result: Parsed planner response.

        Returns:
            Human-readable semantic errors. Empty means validation passed.
        """
        combined_prompt = ", ".join(
            (
                result["prompt"] or "",
                *result["character_prompts"].values(),
            )
        )
        hug_is_negated = bool(
            re.search(
                r"(?:不要|不|没有|禁止|拒绝)\s*(?:互相)?(?:拥抱|抱在一起)|"
                r"\b(?:no|not|without)\s+hugg?",
                description,
                re.IGNORECASE,
            )
        )
        errors: list[str] = []
        for name, source_pattern, output_pattern in SEMANTIC_ANCHOR_RULES:
            if name == "hugging" and hug_is_negated:
                if output_pattern.search(combined_prompt):
                    errors.append("错误增加 hugging")
                continue
            if source_pattern.search(description) and not output_pattern.search(
                combined_prompt
            ):
                errors.append(f"缺少 {name}")
        if re.search(r"推倒|\bpush(?:ing|ed)?\s+(?:down|over)\b", description, re.I):
            main_prompt = result["prompt"] or ""
            if not (
                re.search(r"\bpush", main_prompt, re.I)
                and re.search(
                    r"\b(?:down|over|falling|fallen|on (?:the )?ground|lying)",
                    main_prompt,
                    re.I,
                )
            ):
                errors.append("缺少 push-down 动作结果")
            ordered_slots = list(
                dict.fromkeys(CHARACTER_SLOT_PATTERN.findall(description))
            )
            if len(ordered_slots) >= 2:
                source_prompt = result["character_prompts"].get(ordered_slots[0], "")
                target_prompt = result["character_prompts"].get(ordered_slots[1], "")
                if not re.search(r"\bsource#pushing\b", source_prompt, re.I):
                    errors.append("主动人物缺少 source#pushing")
                if not re.search(r"\btarget#pushing\b", target_prompt, re.I):
                    errors.append("被动人物缺少 target#pushing")
                if not re.search(
                    r"\b(?:standing|leaning|reaching|arm extended|looking down)",
                    source_prompt,
                    re.I,
                ):
                    errors.append("主动人物缺少推人姿态")
                if not re.search(
                    r"\b(?:falling|backward|on (?:the )?ground|lying|looking up)",
                    target_prompt,
                    re.I,
                ):
                    errors.append("被动人物缺少倒地姿态")
        if not PAINTER_SUBJECT_PATTERN.search(description) and re.search(
            r"(?i)(?<![a-z])(?:painter|paintbrush|canvas \(object\)|easel)(?![a-z])",
            combined_prompt,
        ):
            errors.append("凭空增加画师或画具")
        return errors
