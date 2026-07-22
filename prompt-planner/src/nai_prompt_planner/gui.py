"""Tkinter desktop interface for the standalone prompt planner."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from tkinter import font as tkfont
from urllib.parse import urlparse

import httpx

from .planner import (
    DeepSeekPromptPlanner,
    PlannerError,
    PlannerSettings,
    load_system_prompt,
    parse_planner_response,
)
from .tag_cache import DanbooruCacheInfo, read_cache_info, update_danbooru_cache

APP_NAME = "NAI Prompt Planner"
APP_VERSION = "0.1.0"


class PromptPlannerWindow:
    """Own the Tk widgets and one-request-at-a-time UI state."""

    def __init__(self, root: tk.Tk) -> None:
        """Build the desktop window.

        Args:
            root: Tk root window owned by the application.
        """
        self.root = root
        self._closing = False
        self._active_danbooru_validation = False
        self._settings_path = (
            Path(os.environ.get("APPDATA", Path.home()))
            / "NAIPromptPlanner"
            / "settings.json"
        )
        saved = self._load_saved_settings()
        self.api_key_var = tk.StringVar()
        self.base_url_var = tk.StringVar(
            value=str(saved.get("base_url", "https://api.deepseek.com"))
        )
        self.model_var = tk.StringVar(
            value=str(saved.get("model", "deepseek-v4-flash"))
        )
        self.thinking_var = tk.StringVar(value=str(saved.get("thinking", "disabled")))
        self.reasoning_var = tk.StringVar(
            value=str(saved.get("reasoning_effort", "high"))
        )
        self.timeout_var = tk.StringVar(value=str(saved.get("timeout_seconds", "60")))
        self.max_tokens_var = tk.StringVar(value=str(saved.get("max_tokens", "2048")))
        self.max_length_var = tk.StringVar(value=str(saved.get("max_length", "4000")))
        self.json_mode_var = tk.BooleanVar(value=bool(saved.get("json_mode", True)))
        self.danbooru_validation_var = tk.BooleanVar(
            value=bool(saved.get("validate_danbooru_tags", True))
        )
        self.danbooru_min_posts_var = tk.StringVar(
            value=str(saved.get("danbooru_min_post_count", "50"))
        )
        cache_info = read_cache_info()
        self.cache_status_var = tk.StringVar(
            value=(
                f"本地词库 {cache_info.snapshot_date} · {cache_info.tag_count:,} tags"
                if cache_info is not None
                else "本地词库未安装"
            )
        )
        self.show_key_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(
            value="就绪：请输入 DeepSeek API Key 和画面描述。"
        )

        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1080x820")
        self.root.minsize(900, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Microsoft YaHei UI", size=10)
        tkfont.nametofont("TkTextFont").configure(family="Microsoft YaHei UI", size=10)

        container = ttk.Frame(self.root, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=3)
        container.rowconfigure(4, weight=4)

        connection_frame = ttk.LabelFrame(
            container, text="DeepSeek 连接与生成设置", padding=10
        )
        connection_frame.grid(row=0, column=0, sticky="ew")
        for column in (1, 3, 5):
            connection_frame.columnconfigure(column, weight=1)

        ttk.Label(connection_frame, text="API Key").grid(
            row=0, column=0, padx=(0, 6), pady=4, sticky="w"
        )
        self.api_key_entry = ttk.Entry(
            connection_frame,
            textvariable=self.api_key_var,
            show="●",
        )
        self.api_key_entry.grid(
            row=0, column=1, columnspan=3, padx=(0, 8), pady=4, sticky="ew"
        )
        ttk.Checkbutton(
            connection_frame,
            text="显示",
            variable=self.show_key_var,
            command=self._toggle_api_key,
        ).grid(row=0, column=4, pady=4, sticky="w")
        ttk.Label(connection_frame, text="仅驻留内存，不写入配置文件").grid(
            row=0, column=5, pady=4, sticky="e"
        )

        ttk.Label(connection_frame, text="Base URL").grid(
            row=1, column=0, padx=(0, 6), pady=4, sticky="w"
        )
        ttk.Entry(connection_frame, textvariable=self.base_url_var).grid(
            row=1, column=1, columnspan=3, padx=(0, 8), pady=4, sticky="ew"
        )
        ttk.Label(connection_frame, text="模型").grid(
            row=1, column=4, padx=(0, 6), pady=4, sticky="e"
        )
        ttk.Combobox(
            connection_frame,
            textvariable=self.model_var,
            values=("deepseek-v4-flash", "deepseek-v4-pro"),
        ).grid(row=1, column=5, pady=4, sticky="ew")

        ttk.Label(connection_frame, text="Thinking").grid(
            row=2, column=0, padx=(0, 6), pady=4, sticky="w"
        )
        thinking_box = ttk.Combobox(
            connection_frame,
            textvariable=self.thinking_var,
            values=("disabled", "enabled", "omit"),
            state="readonly",
            width=12,
        )
        thinking_box.grid(row=2, column=1, padx=(0, 8), pady=4, sticky="ew")
        thinking_box.bind("<<ComboboxSelected>>", self._update_reasoning_state)
        ttk.Label(connection_frame, text="Reasoning").grid(
            row=2, column=2, padx=(0, 6), pady=4, sticky="e"
        )
        self.reasoning_box = ttk.Combobox(
            connection_frame,
            textvariable=self.reasoning_var,
            values=("high", "max"),
            state="readonly",
            width=10,
        )
        self.reasoning_box.grid(row=2, column=3, padx=(0, 8), pady=4, sticky="ew")
        ttk.Checkbutton(
            connection_frame,
            text="JSON Output",
            variable=self.json_mode_var,
        ).grid(row=2, column=4, pady=4, sticky="e")
        ttk.Label(connection_frame, text="官方接口建议开启").grid(
            row=2, column=5, pady=4, sticky="w"
        )

        ttk.Label(connection_frame, text="超时（秒）").grid(
            row=3, column=0, padx=(0, 6), pady=4, sticky="w"
        )
        ttk.Spinbox(
            connection_frame,
            textvariable=self.timeout_var,
            from_=1,
            to=600,
            increment=1,
            width=10,
        ).grid(row=3, column=1, padx=(0, 8), pady=4, sticky="ew")
        ttk.Label(connection_frame, text="最大输出 Tokens").grid(
            row=3, column=2, padx=(0, 6), pady=4, sticky="e"
        )
        ttk.Spinbox(
            connection_frame,
            textvariable=self.max_tokens_var,
            from_=128,
            to=32768,
            increment=128,
            width=12,
        ).grid(row=3, column=3, padx=(0, 8), pady=4, sticky="ew")
        ttk.Label(connection_frame, text="Prompt 字符上限").grid(
            row=3, column=4, padx=(0, 6), pady=4, sticky="e"
        )
        ttk.Spinbox(
            connection_frame,
            textvariable=self.max_length_var,
            from_=1,
            to=20000,
            increment=100,
            width=12,
        ).grid(row=3, column=5, pady=4, sticky="ew")

        ttk.Checkbutton(
            connection_frame,
            text="严格本地 Danbooru 校验",
            variable=self.danbooru_validation_var,
        ).grid(row=4, column=0, columnspan=2, pady=4, sticky="w")
        ttk.Label(connection_frame, text="最低作品数").grid(
            row=4, column=2, padx=(0, 6), pady=4, sticky="e"
        )
        ttk.Spinbox(
            connection_frame,
            textvariable=self.danbooru_min_posts_var,
            from_=50,
            to=1_000_000,
            increment=50,
            width=12,
        ).grid(row=4, column=3, padx=(0, 8), pady=4, sticky="ew")
        self.update_tags_button = ttk.Button(
            connection_frame,
            text="更新本地词库",
            command=self._start_tag_cache_update,
        )
        self.update_tags_button.grid(row=4, column=4, padx=(0, 8), pady=4, sticky="e")
        ttk.Label(connection_frame, textvariable=self.cache_status_var).grid(
            row=4, column=5, pady=4, sticky="e"
        )

        request_label = ttk.Label(container, text="画面描述")
        request_label.grid(row=1, column=0, pady=(10, 4), sticky="w")
        self.description_text = scrolledtext.ScrolledText(
            container,
            wrap=tk.WORD,
            undo=True,
            height=8,
            padx=8,
            pady=8,
        )
        self.description_text.grid(row=2, column=0, sticky="nsew")

        action_frame = ttk.Frame(container)
        action_frame.grid(row=3, column=0, pady=10, sticky="ew")
        action_frame.columnconfigure(6, weight=1)
        self.generate_button = ttk.Button(
            action_frame,
            text="生成 Prompt",
            command=self._start_plan,
        )
        self.generate_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(action_frame, text="清空描述", command=self._clear_description).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(
            action_frame, text="本地自检", command=self._run_local_self_test
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(action_frame, text="复制主 Prompt", command=self._copy_prompt).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(action_frame, text="复制完整 JSON", command=self._copy_json).grid(
            row=0, column=4, padx=(0, 8)
        )
        self.progress = ttk.Progressbar(action_frame, mode="indeterminate", length=140)
        self.progress.grid(row=0, column=5, padx=(4, 12))
        self.progress.grid_remove()
        ttk.Label(action_frame, textvariable=self.status_var).grid(
            row=0, column=6, sticky="e"
        )

        self.output_tabs = ttk.Notebook(container)
        self.output_tabs.grid(row=4, column=0, sticky="nsew")
        self.prompt_text = self._add_output_tab("主 Prompt")
        self.character_text = self._add_output_tab("人物 Prompts")
        self.json_text = self._add_output_tab("完整 JSON")
        self._update_reasoning_state()
        self.api_key_entry.focus_set()

    def _add_output_tab(self, title: str) -> scrolledtext.ScrolledText:
        """Create one read-only-style result tab.

        Args:
            title: Notebook tab title.

        Returns:
            Text widget used to display the result.
        """
        frame = ttk.Frame(self.output_tabs, padding=6)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text_widget = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            padx=8,
            pady=8,
        )
        text_widget.grid(row=0, column=0, sticky="nsew")
        self.output_tabs.add(frame, text=title)
        return text_widget

    def _load_saved_settings(self) -> dict[str, object]:
        """Load non-secret UI settings from the current user profile.

        Returns:
            Parsed settings or an empty mapping when unavailable.
        """
        try:
            payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_settings(self) -> None:
        """Persist only non-secret UI settings for the next launch."""
        payload = {
            "base_url": self.base_url_var.get().strip(),
            "model": self.model_var.get().strip(),
            "thinking": self.thinking_var.get(),
            "reasoning_effort": self.reasoning_var.get(),
            "timeout_seconds": self.timeout_var.get().strip(),
            "max_tokens": self.max_tokens_var.get().strip(),
            "max_length": self.max_length_var.get().strip(),
            "json_mode": self.json_mode_var.get(),
            "validate_danbooru_tags": self.danbooru_validation_var.get(),
            "danbooru_min_post_count": self.danbooru_min_posts_var.get().strip(),
        }
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _collect_request(self) -> tuple[PlannerSettings, str, int]:
        """Validate all fields before starting a background request.

        Returns:
            Settings, description, and maximum prompt length.

        Raises:
            PlannerError: If any visible input is invalid.
        """
        api_key = self.api_key_var.get().strip()
        base_url = self.base_url_var.get().strip().rstrip("/")
        model = self.model_var.get().strip()
        description = self.description_text.get("1.0", tk.END).strip()
        try:
            timeout_seconds = float(self.timeout_var.get())
            max_tokens = int(self.max_tokens_var.get())
            max_length = int(self.max_length_var.get())
            danbooru_min_post_count = int(self.danbooru_min_posts_var.get())
        except ValueError as exc:
            raise PlannerError(
                "invalid_config",
                "超时、Tokens、Prompt 上限和 Danbooru 最低作品数必须是数字。",
            ) from exc
        if not api_key:
            raise PlannerError("missing_api_key", "请输入 DeepSeek API Key。")
        if not base_url.startswith(("https://", "http://")):
            raise PlannerError(
                "invalid_config", "Base URL 必须以 http:// 或 https:// 开头。"
            )
        parsed_url = urlparse(base_url)
        if parsed_url.scheme == "http" and parsed_url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise PlannerError(
                "invalid_config",
                "非本机 DeepSeek 地址必须使用 HTTPS，避免 API Key 明文传输。",
            )
        if not model:
            raise PlannerError("invalid_config", "模型名称不能为空。")
        if self.thinking_var.get() not in {"disabled", "enabled", "omit"}:
            raise PlannerError("invalid_config", "Thinking 配置无效。")
        if self.reasoning_var.get() not in {"high", "max"}:
            raise PlannerError("invalid_config", "Reasoning 配置无效。")
        if not 1 <= timeout_seconds <= 600:
            raise PlannerError("invalid_config", "超时必须在 1 到 600 秒之间。")
        if not 128 <= max_tokens <= 32768:
            raise PlannerError(
                "invalid_config", "最大输出 Tokens 必须在 128 到 32768 之间。"
            )
        if not 1 <= max_length <= 20000:
            raise PlannerError(
                "invalid_config", "Prompt 字符上限必须在 1 到 20000 之间。"
            )
        if not 50 <= danbooru_min_post_count <= 1_000_000:
            raise PlannerError(
                "invalid_config", "Danbooru 最低作品数必须在 50 到 1000000 之间。"
            )
        if not description:
            raise PlannerError("invalid_request", "请输入画面描述。")
        return (
            PlannerSettings(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                thinking=self.thinking_var.get(),
                reasoning_effort=self.reasoning_var.get(),
                json_mode=self.json_mode_var.get(),
                validate_danbooru_tags=self.danbooru_validation_var.get(),
                danbooru_min_post_count=danbooru_min_post_count,
            ),
            description,
            max_length,
        )

    def _start_plan(self) -> None:
        """Validate the form and launch one non-blocking planner request."""
        try:
            settings, description, max_length = self._collect_request()
        except PlannerError as exc:
            self.status_var.set(str(exc))
            messagebox.showwarning(APP_NAME, str(exc), parent=self.root)
            return
        self._save_settings()
        self._active_danbooru_validation = settings.validate_danbooru_tags
        self.generate_button.configure(state=tk.DISABLED)
        self.update_tags_button.configure(state=tk.DISABLED)
        self.progress.grid()
        self.progress.start(12)
        self.status_var.set(f"正在请求 {settings.model}…")
        worker = threading.Thread(
            target=self._run_plan_worker,
            args=(settings, description, max_length),
            daemon=True,
        )
        worker.start()

    def _run_plan_worker(
        self,
        settings: PlannerSettings,
        description: str,
        max_length: int,
    ) -> None:
        """Execute the async planner from a background thread.

        Args:
            settings: Immutable request settings copied from the form.
            description: User scene description copied from the form.
            max_length: Requested combined Prompt character limit.
        """

        async def execute() -> dict[str, object]:
            planner = DeepSeekPromptPlanner(settings)
            try:
                return await planner.plan(description, max_length)
            finally:
                await planner.aclose()

        try:
            result = asyncio.run(execute())
            error: PlannerError | None = None
        except PlannerError as exc:
            result = {}
            error = exc
        except Exception:
            result = {}
            error = PlannerError("unexpected_error", "程序发生未预期错误。")
        if not self._closing:
            self.root.after(0, self._finish_plan, result, error)

    def _finish_plan(
        self,
        result: dict[str, object],
        error: PlannerError | None,
    ) -> None:
        """Render one completed background request on the Tk thread.

        Args:
            result: Validated strict planner result, if successful.
            error: Safe categorized error, if the request failed.
        """
        self.progress.stop()
        self.progress.grid_remove()
        self.generate_button.configure(state=tk.NORMAL)
        self.update_tags_button.configure(state=tk.NORMAL)
        if error is not None:
            self.status_var.set(f"失败：{error}")
            messagebox.showerror(APP_NAME, str(error), parent=self.root)
            return
        prompt = result.get("prompt") or ""
        character_prompts = result.get("character_prompts") or {}
        full_json = json.dumps(result, ensure_ascii=False, indent=2)
        self._replace_text(self.prompt_text, str(prompt))
        self._replace_text(
            self.character_text,
            json.dumps(character_prompts, ensure_ascii=False, indent=2),
        )
        self._replace_text(self.json_text, full_json)
        if result.get("ok"):
            if self._active_danbooru_validation:
                self.status_var.set(
                    "完成：Prompt 已通过协议、语义与本地 Danbooru 词库校验。"
                )
            else:
                self.status_var.set("完成：Prompt 已通过本地协议与语义校验。")
            self.output_tabs.select(0)
        else:
            self.status_var.set("描述包含无法消解的互斥约束。")
            self.output_tabs.select(2)

    @staticmethod
    def _replace_text(widget: scrolledtext.ScrolledText, content: str) -> None:
        """Replace one result widget's complete content.

        Args:
            widget: Target result text widget.
            content: New displayed text.
        """
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)

    def _toggle_api_key(self) -> None:
        """Toggle API Key masking without changing its value."""
        self.api_key_entry.configure(show="" if self.show_key_var.get() else "●")

    def _update_reasoning_state(self, _event: object | None = None) -> None:
        """Enable reasoning effort only when thinking is enabled."""
        state = "readonly" if self.thinking_var.get() == "enabled" else tk.DISABLED
        self.reasoning_box.configure(state=state)

    def _clear_description(self) -> None:
        """Clear only the input description."""
        self.description_text.delete("1.0", tk.END)
        self.description_text.focus_set()

    def _run_local_self_test(self) -> None:
        """Verify packaged resources without sending an API request."""
        if _self_test() == 0:
            self.status_var.set("本地自检通过：Prompt 资源和解析器可用，未调用 API。")
            messagebox.showinfo(
                APP_NAME,
                "本地自检通过。\n本次检查没有调用 DeepSeek API。",
                parent=self.root,
            )
            return
        self.status_var.set("本地自检失败。")
        messagebox.showerror(
            APP_NAME,
            "本地自检失败，请重新下载或打包程序。",
            parent=self.root,
        )

    def _start_tag_cache_update(self) -> None:
        """Download and install the latest local vocabulary snapshot."""
        self.generate_button.configure(state=tk.DISABLED)
        self.update_tags_button.configure(state=tk.DISABLED)
        self.progress.grid()
        self.progress.start(12)
        self.status_var.set("正在下载并构建本地 Danbooru 词库…")
        worker = threading.Thread(
            target=self._run_tag_cache_update_worker,
            daemon=True,
        )
        worker.start()

    def _run_tag_cache_update_worker(self) -> None:
        """Build the local vocabulary from a background thread."""
        try:
            info = asyncio.run(update_danbooru_cache())
            error: str | None = None
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            info = None
            error = str(exc)
        except Exception:
            info = None
            error = "程序发生未预期错误。"
        if not self._closing:
            self.root.after(0, self._finish_tag_cache_update, info, error)

    def _finish_tag_cache_update(
        self,
        info: DanbooruCacheInfo | None,
        error: str | None,
    ) -> None:
        """Render one completed local vocabulary update.

        Args:
            info: Cache metadata returned by the updater.
            error: Safe update error text, if any.
        """
        self.progress.stop()
        self.progress.grid_remove()
        self.generate_button.configure(state=tk.NORMAL)
        self.update_tags_button.configure(state=tk.NORMAL)
        if error is not None or info is None:
            message = error or "本地词库更新失败。"
            self.status_var.set(f"词库更新失败：{message}")
            messagebox.showerror(APP_NAME, message, parent=self.root)
            return
        self.cache_status_var.set(
            f"本地词库 {info.snapshot_date} · {info.tag_count:,} tags"
        )
        self.status_var.set("本地 Danbooru 词库更新完成；生成时不再联网校验。")

    def _copy_prompt(self) -> None:
        """Copy the current main Prompt to the Windows clipboard."""
        self._copy_widget(self.prompt_text, "主 Prompt")

    def _copy_json(self) -> None:
        """Copy the current complete JSON to the Windows clipboard."""
        self._copy_widget(self.json_text, "完整 JSON")

    def _copy_widget(
        self,
        widget: scrolledtext.ScrolledText,
        label: str,
    ) -> None:
        """Copy one non-empty result widget.

        Args:
            widget: Source text widget.
            label: User-facing copied content name.
        """
        content = widget.get("1.0", tk.END).strip()
        if not content:
            self.status_var.set(f"没有可复制的{label}。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set(f"已复制{label}。")

    def _on_close(self) -> None:
        """Persist non-secret settings and close the window."""
        self._closing = True
        self._save_settings()
        self.root.destroy()


def _self_test() -> int:
    """Verify packaged prompt resources without opening a window.

    Returns:
        Zero when resources and strict parsing are available.
    """
    system_prompt = load_system_prompt()
    if "character_prompts" not in system_prompt or "qualityToggle" not in system_prompt:
        return 2
    result = parse_planner_response(
        '{"ok":true,"prompt":"1girl, solo, layered dress",'
        '"character_prompts":{},"error":null}',
        4000,
    )
    return 0 if result["ok"] else 3


def main() -> None:
    """Start the GUI or run the hidden packaged-resource self-test."""
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    PromptPlannerWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
