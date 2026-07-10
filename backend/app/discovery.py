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
_GET_MCP_TEMPLATE_PATH = "/api/grpc/getMcpTemplate"

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
            "tools": [
                {
                    "name": "pdf_text_extractor",
                    "description": "Extracts text from unstructured medical PDFs.",
                }
            ],
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

PLATFORM_BASELINES: dict[str, Any] = {
    "models": ["llama-3-70b-instruct"],
    "mcp_servers": list(SANDBOX_DEFAULTS["mcp_servers"]),
    "tools": [],
}


class NamedEntry(BaseModel):
    name: str
    description: str = ""


class McpServerEntry(BaseModel):
    name: str
    description: str = ""
    tools: list[NamedEntry] = Field(default_factory=list)


class DiscoveryResponse(BaseModel):
    models: list[str] = Field(default_factory=list)
    default_model: Optional[str] = Field(
        default=None,
        description="Studio default LLM from listModels (is_studio_default=true).",
    )
    mcp_servers: list[McpServerEntry] = Field(default_factory=list)
    tools: list[NamedEntry] = Field(default_factory=list)
    discovery_active: bool
    source: str = Field(description="'platform' when live discovery succeeded, else 'sandbox'")
    degraded_reason: Optional[str] = Field(
        default=None,
        description="Set when discovery_active=false; safe diagnostic (never includes token values).",
    )


def _resolve_token(user_token: Optional[str]) -> tuple[Optional[str], str]:
    env_token = os.environ.get("CLOUDERA_AI_TOKEN", "").strip()
    if env_token:
        return env_token, "env:CLOUDERA_AI_TOKEN"
    if user_token and user_token.strip():
        return user_token.strip(), "request:cookie_or_bearer"
    return None, "none"


def _item_name(item: dict[str, Any]) -> Optional[str]:
    name = item.get("name") or item.get("displayName") or item.get("title")
    return str(name) if name else None


def _item_description(item: dict[str, Any]) -> str:
    for key in (
        "description",
        "toolDescription",
        "tool_description",
        "summary",
        "desc",
        "shortDescription",
    ):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _entry_from_item(item: dict[str, Any]) -> Optional[NamedEntry]:
    name = _item_name(item)
    if not name:
        return None
    return NamedEntry(name=name, description=_item_description(item))


def _tools_with_descriptions(entries: list[NamedEntry]) -> list[NamedEntry]:
    return [e for e in entries if e.description.strip()]


def _parse_models(payload: dict[str, Any]) -> tuple[list[str], Optional[str]]:
    """Parse listModels response; return all names and the is_studio_default model."""
    models: list[str] = []
    studio_default: Optional[str] = None
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        model_name = str(name)
        models.append(model_name)
        if item.get("is_studio_default") is True:
            studio_default = model_name
    return models, studio_default


def _chosen_model(
    models: list[str],
    studio_default: Optional[str],
    *,
    sandbox: bool = False,
) -> Optional[str]:
    if studio_default and studio_default in models:
        return studio_default
    if sandbox:
        return str(PLATFORM_BASELINES["models"][0]) if PLATFORM_BASELINES["models"] else None
    if models:
        return models[0]
    baselines = list(PLATFORM_BASELINES["models"])
    return str(baselines[0]) if baselines else None


def _parse_tool_templates(payload: dict[str, Any]) -> list[NamedEntry]:
    for key in ("templates", "toolTemplates", "tools"):
        items = payload.get(key)
        if isinstance(items, list):
            entries: list[NamedEntry] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                entry = _entry_from_item(item)
                if entry:
                    entries.append(entry)
            return _tools_with_descriptions(entries)
    return []


def _raw_mcp_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("templates", "mcpTemplates", "mcp_templates"):
        items = payload.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _dedupe_mcp_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        name = _item_name(item)
        if not name or name in seen:
            continue
        seen[name] = item
    return list(seen.values())


def _parse_tools_from_mcp_detail(payload: dict[str, Any]) -> list[NamedEntry]:
    entries: list[NamedEntry] = []
    for key in ("tools", "toolTemplates", "templates"):
        items = payload.get(key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                entry = _entry_from_item(item)
                if entry:
                    entries.append(entry)

    for wrapper_key in ("template", "mcpTemplate", "mcp_template"):
        wrapped = payload.get(wrapper_key)
        if isinstance(wrapped, dict):
            entries.extend(_parse_tools_from_mcp_detail(wrapped))

    return _tools_with_descriptions(entries)


def _get_mcp_template_body(item: dict[str, Any]) -> dict[str, str]:
    for key in ("id", "templateId", "template_id"):
        value = item.get(key)
        if value:
            return {"id": str(value)}
    name = _item_name(item)
    if name:
        return {"name": name}
    return {}


def _mcp_server_from_dict(data: dict[str, Any]) -> McpServerEntry:
    tools = [
        NamedEntry(**t) for t in data.get("tools", []) if isinstance(t, dict) and t.get("name")
    ]
    return McpServerEntry(
        name=str(data["name"]),
        description=str(data.get("description") or ""),
        tools=_tools_with_descriptions(tools),
    )


def _apply_model_baselines(models: list[str]) -> list[str]:
    if models:
        return models
    return list(PLATFORM_BASELINES["models"])


def _apply_tool_baselines(tools: list[NamedEntry]) -> list[NamedEntry]:
    if tools:
        return tools
    baseline_tools = [
        NamedEntry(**e) for e in PLATFORM_BASELINES["tools"] if e.get("description")
    ]
    return _tools_with_descriptions(baseline_tools)


def _apply_mcp_baselines(mcp_servers: list[McpServerEntry]) -> list[McpServerEntry]:
    if mcp_servers:
        return mcp_servers
    return [_mcp_server_from_dict(e) for e in PLATFORM_BASELINES["mcp_servers"]]


async def _post_grpc(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    url = f"{config.DISCOVERY_BASE_URL.rstrip('/')}{path}"
    payload = json.dumps(body if body is not None else {})
    response = await client.post(
        url,
        content=payload,
        headers=_DISCOVERY_HEADERS,
        cookies={"_cdswuserstoken": token},
    )
    response.raise_for_status()
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise ValueError(f"Unexpected JSON shape from {path}")
    return parsed


async def _fetch_mcp_server(
    client: httpx.AsyncClient, token: str, item: dict[str, Any]
) -> McpServerEntry:
    name = _item_name(item) or "unknown"
    description = _item_description(item)
    body = _get_mcp_template_body(item)
    tools: list[NamedEntry] = []
    if body:
        try:
            detail = await _post_grpc(client, _GET_MCP_TEMPLATE_PATH, token, body)
            tools = _parse_tools_from_mcp_detail(detail)
        except Exception as exc:
            logger.warning("getMcpTemplate failed for %s: %s", name, exc)
    return McpServerEntry(name=name, description=description, tools=tools)


async def _fetch_mcp_servers_with_tools(
    client: httpx.AsyncClient, token: str, mcp_payload: dict[str, Any]
) -> list[McpServerEntry]:
    items = _dedupe_mcp_items(_raw_mcp_items(mcp_payload))
    if not items:
        return []
    servers = await asyncio.gather(
        *[_fetch_mcp_server(client, token, item) for item in items]
    )
    return list(servers)


def _sandbox_response(reason: str) -> DiscoveryResponse:
    logger.warning("Discovery sandbox fallback: %s", reason)
    models = list(SANDBOX_DEFAULTS["models"])
    return DiscoveryResponse(
        models=models,
        default_model=_chosen_model(models, None, sandbox=True),
        mcp_servers=[_mcp_server_from_dict(e) for e in SANDBOX_DEFAULTS["mcp_servers"]],
        tools=_tools_with_descriptions([NamedEntry(**e) for e in SANDBOX_DEFAULTS["tools"]]),
        discovery_active=False,
        source="sandbox",
        degraded_reason=reason,
    )


async def _fetch_live_platform(token: str) -> DiscoveryResponse:
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_S) as client:
        models_payload = await _post_grpc(client, _LIST_MODELS_PATH, token)
        mcp_payload = await _post_grpc(client, _LIST_MCP_TEMPLATES_PATH, token)
        tools_payload = await _post_grpc(client, _LIST_TOOL_TEMPLATES_PATH, token)
        mcp_servers = await _fetch_mcp_servers_with_tools(client, token, mcp_payload)

    raw_models, studio_default = _parse_models(models_payload)
    models = _apply_model_baselines(raw_models)
    default_model = _chosen_model(models, studio_default)
    mcp_servers = _apply_mcp_baselines(mcp_servers)
    tools = _apply_tool_baselines(_parse_tool_templates(tools_payload))

    return DiscoveryResponse(
        models=models,
        default_model=default_model,
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
    result = await _fetch_live_platform(token)
    return {
        "models": result.models,
        "default_model": result.default_model,
        "mcp_servers": [e.model_dump() for e in result.mcp_servers],
        "tools": [e.model_dump() for e in result.tools],
        "discovery_active": result.discovery_active,
        "source": result.source,
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
