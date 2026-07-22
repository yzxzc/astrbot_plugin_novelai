"""Command-line interface for planning and serving prompts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from .planner import DeepSeekPromptPlanner, PlannerError, PlannerSettings
from .tag_cache import update_danbooru_cache


def _build_parser() -> argparse.ArgumentParser:
    """Build the small two-command CLI parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="nai-prompt-planner",
        description="Plan NovelAI V4.5 prompts through a standalone DeepSeek API.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="Plan one description")
    plan_parser.add_argument(
        "description",
        nargs="?",
        help="Natural-language description; stdin is used when omitted",
    )
    plan_parser.add_argument("--max-length", type=int, default=4000)
    subparsers.add_parser(
        "update-tags",
        help="Download and rebuild the local Danbooru vocabulary",
    )
    serve_parser = subparsers.add_parser("serve", help="Start the local HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


async def _run_plan(description: str, max_length: int) -> int:
    """Run one CLI planning request.

    Args:
        description: User-provided natural-language description.
        max_length: Maximum combined result character count.

    Returns:
        Process exit code.
    """
    planner: DeepSeekPromptPlanner | None = None
    try:
        settings = PlannerSettings.from_env()
        planner = DeepSeekPromptPlanner(settings)
        result = await planner.plan(description, max_length)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result["ok"] else 2
    except PlannerError as exc:
        print(
            json.dumps(
                {"ok": False, "error": exc.code, "message": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if planner is not None:
            await planner.aclose()


def main() -> None:
    """Dispatch the CLI and terminate with its result code."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "plan":
        description = args.description
        if description is None:
            if sys.stdin.isatty():
                parser.error("plan requires a description or piped stdin")
            description = sys.stdin.read().strip()
        raise SystemExit(asyncio.run(_run_plan(description, args.max_length)))

    if args.command == "update-tags":
        try:
            info = asyncio.run(update_danbooru_cache())
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {
                    "snapshot_date": info.snapshot_date,
                    "tag_count": info.tag_count,
                    "alias_count": info.alias_count,
                    "path": str(info.path),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    settings = PlannerSettings.from_env()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (
        settings.service_token
    ):
        parser.error(
            "PLANNER_SERVICE_TOKEN is required when listening outside localhost"
        )
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn

    uvicorn.run(
        "nai_prompt_planner.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
