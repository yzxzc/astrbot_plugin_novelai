"""FastAPI surface for the standalone prompt planner."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .planner import DeepSeekPromptPlanner, PlannerError, PlannerSettings


class PlanRequest(BaseModel):
    """One natural-language planning request."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=20_000)
    max_length: int = Field(default=4000, ge=1, le=20_000)


class PlanResponse(BaseModel):
    """Strict caller-side planning response."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    prompt: str | None
    character_prompts: dict[str, str]
    error: str | None


class HealthResponse(BaseModel):
    """Non-sensitive service readiness response."""

    ready: bool
    model: str
    error: str | None = None


def create_app() -> FastAPI:
    """Create one API app with a shared DeepSeek HTTP client lifecycle.

    Returns:
        Configured FastAPI application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            settings = PlannerSettings.from_env()
            app.state.settings = settings
            app.state.config_error = None
            app.state.planner = DeepSeekPromptPlanner(settings)
        except PlannerError as exc:
            app.state.settings = None
            app.state.planner = None
            app.state.config_error = str(exc)
        yield
        if app.state.planner is not None:
            await app.state.planner.aclose()

    app = FastAPI(
        title="NAI Prompt Planner",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        """Report readiness without exposing credentials or upstream URL."""
        settings = request.app.state.settings
        if settings is None:
            return HealthResponse(
                ready=False,
                model="unconfigured",
                error=request.app.state.config_error,
            )
        return HealthResponse(
            ready=bool(settings.api_key),
            model=settings.model,
            error=None if settings.api_key else "DEEPSEEK_API_KEY is not configured",
        )

    @app.post("/v1/plan", response_model=PlanResponse)
    async def plan(
        payload: PlanRequest,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> PlanResponse:
        """Plan one description after optional service-token authentication."""
        settings = request.app.state.settings
        planner = request.app.state.planner
        if settings is None or planner is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "invalid_config",
                    "message": request.app.state.config_error,
                },
            )
        if settings.service_token:
            expected = f"Bearer {settings.service_token}"
            if authorization is None or not secrets.compare_digest(
                authorization, expected
            ):
                raise HTTPException(
                    status_code=401,
                    detail={"code": "unauthorized", "message": "Bearer Token 无效。"},
                )
        try:
            result = await planner.plan(payload.description, payload.max_length)
        except PlannerError as exc:
            status_code = {
                "invalid_request": 422,
                "missing_api_key": 503,
                "deepseek_rate_limit": 429,
            }.get(exc.code, 502)
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        return PlanResponse(**result)

    return app


app = create_app()
