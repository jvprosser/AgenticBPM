"""Step 5b — Cloudera Agent Studio capability discovery with soft-degrade sandbox."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from . import config

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_S = float(os.environ.get("DISCOVERY_TIMEOUT_S", "15"))

_LIST_MODELS_PATH = "/api/grpc/listModels"
_LIST_MCP_TEMPLATES_PATH = "/api/grpc/listMcpTemplates"
_LIST_TOOL_TEMPLATES_PATH = "/api/grpc/listToolTemplates"

_DISCOVERY_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://cai-agent-studio-z516vb.ml-dbfc64d1-783.go01-dem.ylcu-atmi.cloudera.site",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
}

SANDBOX_DEFAULTS: dict[str, Any] = {
    "models": ["llama-3-70b-instruct", "mistral-large"],
    "mcp_servers": [
        {
            "name": "Guidewire Claims Core",
            "description": "Exposes read-only access to legacy insurance database tables.",
        }
    ],
    "tools": [
        {
            "name": "pdf_text_extractor",
            "description": "Extracts text from unstructured medical PDFs.",
        },
        {
            "name": "code_execution",
            "description": "Executes sandboxed Python logic for financial ledger reconciliation.",
        },
    ],
}

# Injected per-array when platform returns 200 OK with empty lists (§4.1.3 / §4.1.4).
PLATFORM_BASELINES: dict[str, Any] = {
    "models": ["llama-3-70b-instruct"],
    "tools": [
        {"name": "code_execution", "description": ""},
        {"name": "vector_search", "description": ""},
    ],
}


class NamedEntry(BaseModel):
    name: str
    description: str = ""


class DiscoveryResponse(BaseModel):
    models: list[str] = Field(default_factory=list)
    mcp_servers: list[NamedEntry] = Field(default_factory=list)
    tools: list[NamedEntry] = Field(default_factory=list)
    discovery_active: bool
    source: str = Field(description="'platform' when live discovery succeeded, else 'sandbox'")
    degraded_reason: Optional[str] = Field(
        default=None,
        description="Set when discovery_active=false; safe diagnostic (never includes token values).",
    )


def _resolve_token(user_token: Optional[str]) -> tuple[Optional[str], str]:
    """Return (token, source_label) for diagnostics — never log the token itself."""
    env_token = os.environ.get("CLOUDERA_AI_TOKEN", "").strip()
    if env_token:
        return env_token, "env:CLOUDERA_AI_TOKEN"
    if user_token and user_token.strip():
        return user_token.strip(), "request:cookie_or_bearer"
    return None, "none"


def _sandbox_response(reason: str) -> DiscoveryResponse:
    logger.warning("Discovery sandbox fallback: %s", reason)
    return DiscoveryResponse(
        models=list(SANDBOX_DEFAULTS["models"]),
        mcp_servers=[NamedEntry(**e) for e in SANDBOX_DEFAULTS["mcp_servers"]],
        tools=[NamedEntry(**e) for e in SANDBOX_DEFAULTS["tools"]],
        discovery_active=False,
        source="sandbox",
        degraded_reason=reason,
    )


def _parse_models(payload: dict[str, Any]) -> list[str]:
    models: list[str] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name:
            models.append(str(name))
    return models


def _parse_templates(payload: dict[str, Any]) -> list[NamedEntry]:
    entries: list[NamedEntry] = []
    for item in payload.get("templates", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        entries.append(
            NamedEntry(
                name=str(name),
                description=str(item.get("description") or ""),
            )
        )
    return entries


def _apply_model_baselines(models: list[str]) -> list[str]:
    if models:
        return models
    return list(PLATFORM_BASELINES["models"])


def _apply_tool_baselines(tools: list[NamedEntry]) -> list[NamedEntry]:
    if tools:
        return tools
    return [NamedEntry(**e) for e in PLATFORM_BASELINES["tools"]]


async def _post_grpc_list(
    client: httpx.AsyncClient, path: str, token: str
) -> dict[str, Any]:
    url = f"{config.DISCOVERY_BASE_URL.rstrip('/')}{path}"
    response = await client.post(
        url,
        content="{}",
        headers=_DISCOVERY_HEADERS,
        cookies={"_cdswuserstoken": token},
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(f"Unexpected JSON shape from {path}")
    return body


async def _fetch_live_platform(token: str) -> DiscoveryResponse:
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_S) as client:
        models_payload = await _post_grpc_list(client, _LIST_MODELS_PATH, token)
        mcp_payload = await _post_grpc_list(client, _LIST_MCP_TEMPLATES_PATH, token)
        tools_payload = await _post_grpc_list(client, _LIST_TOOL_TEMPLATES_PATH, token)

    models = _apply_model_baselines(_parse_models(models_payload))
    mcp_servers = _parse_templates(mcp_payload)
    tools = _apply_tool_baselines(_parse_templates(tools_payload))

    return DiscoveryResponse(
        models=models,
        mcp_servers=mcp_servers,
        tools=tools,
        discovery_active=True,
        source="platform",
    )


async def fetch_platform_capabilities(user_token: Optional[str]) -> DiscoveryResponse:
    """Resolve auth token and query Agent Studio, soft-degrading to sandbox on failure."""
    token, token_source = _resolve_token(user_token)
    if not token:
        return _sandbox_response(
            "no_auth_token (set CLOUDERA_AI_TOKEN on the backend, or pass _cdswuserstoken "
            "cookie / Authorization: Bearer header from a logged-in Cloudera AI session)"
        )

    try:
        return await _fetch_live_platform(token)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            body = exc.response.json()
            detail = body.get("reason") or body.get("error") or str(body)[:120]
        except Exception:
            detail = exc.response.text[:120]
        return _sandbox_response(
            f"platform_http_{status} via {token_source} ({detail})"
        )
    except httpx.TimeoutException:
        return _sandbox_response(
            f"platform_timeout after {DISCOVERY_TIMEOUT_S}s via {token_source}"
        )
    except Exception as exc:
        return _sandbox_response(f"platform_error via {token_source} ({type(exc).__name__}: {exc})")


async def _run_discovery_probe(token: str) -> dict[str, Any]:
    """Standalone probe: fetch and return filtered capability structures."""
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_S) as client:
        models_payload = await _post_grpc_list(client, _LIST_MODELS_PATH, token)
        mcp_payload = await _post_grpc_list(client, _LIST_MCP_TEMPLATES_PATH, token)
        tools_payload = await _post_grpc_list(client, _LIST_TOOL_TEMPLATES_PATH, token)

    models = _apply_model_baselines(_parse_models(models_payload))
    mcp_servers = _parse_templates(mcp_payload)
    tools = _apply_tool_baselines(_parse_templates(tools_payload))

    return {
        "models": models,
        "mcp_servers": [e.model_dump() for e in mcp_servers],
        "tools": [e.model_dump() for e in tools],
        "discovery_active": True,
        "source": "platform",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    probe_token = os.environ.get("CLOUDERA_AI_TOKEN", "").strip()
    if not probe_token and len(sys.argv) > 1:
        probe_token = sys.argv[1].strip()

    if not probe_token:
        print(
            "Usage: CLOUDERA_AI_TOKEN=<token> python -m app.discovery\n"
            "   or: python -m app.discovery <token>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = asyncio.run(_run_discovery_probe(probe_token))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Platform error (falling back to sandbox): {exc}", file=sys.stderr)
        sandbox = _sandbox_response(f"probe_failed ({exc})")
        print(json.dumps(sandbox.model_dump(), indent=2))
        sys.exit(1)
