"""Platform-proxied semantic data-source typeahead for the Task Dialog."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from . import config, discovery
from .schemas.source_suggest import (
    DataSourceMatch,
    RESOLVE_DATA_SOURCE_INTENT,
    ResolveDataSourceAgentRequest,
)

logger = logging.getLogger(__name__)

_BROKER_TIMEOUT_S = float(__import__("os").environ.get("SOURCE_SUGGEST_TIMEOUT_S", "12"))

_MATCH_LIST_KEYS = (
    "matches",
    "suggestions",
    "data_sources",
    "sources",
    "results",
)


def _compile_infrastructure_catalog(
    capabilities: discovery.DiscoveryResponse,
) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for tool in capabilities.tools:
        catalog.append(
            {
                "kind": "tool",
                "name": tool.name,
                "description": tool.description,
            }
        )
    for server in capabilities.mcp_servers:
        catalog.append(
            {
                "kind": "mcp_server",
                "name": server.name,
                "description": server.description,
                "tools": [entry.model_dump() for entry in server.tools],
            }
        )
    return catalog


def _build_broker_payload(
    user_raw_input: str,
    capabilities: discovery.DiscoveryResponse,
) -> dict[str, Any]:
    request = ResolveDataSourceAgentRequest(
        instruction_intent=RESOLVE_DATA_SOURCE_INTENT,
        user_raw_input=user_raw_input,
        infrastructure_catalog=_compile_infrastructure_catalog(capabilities),
    )
    return request.model_dump()


def _normalize_match_entries(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in _MATCH_LIST_KEYS:
            nested = raw.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        if raw.get("source_name"):
            return [raw]
    return []


def _parse_agent_matches(agent_raw: dict[str, Any]) -> list[DataSourceMatch]:
    candidates: list[Any] = [agent_raw]
    for key in ("data", "result", "response", "output"):
        nested = agent_raw.get(key)
        if isinstance(nested, (dict, list)):
            candidates.append(nested)

    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        entries.extend(_normalize_match_entries(candidate))
        if entries:
            break

    matches: list[DataSourceMatch] = []
    seen_names: set[str] = set()
    for entry in entries:
        try:
            match = DataSourceMatch.model_validate(entry)
        except ValidationError:
            continue
        if not match.source_name:
            continue
        key = match.source_name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        matches.append(match)
    return matches


async def _execute_data_broker(token: str, body: dict[str, Any]) -> dict[str, Any]:
    return await discovery.post_platform_json(
        config.EXECUTE_AGENT_PATH,
        token,
        body,
        timeout=_BROKER_TIMEOUT_S,
    )


async def suggest_data_sources(
    user_raw_input: str,
    user_token: Optional[str],
) -> list[dict[str, Any]]:
    """Return semantic enterprise data-source matches; degrade to [] on broker failure."""
    query = user_raw_input.strip()
    if not query:
        return []

    token, _ = discovery.resolve_platform_token(user_token)
    if not token:
        logger.warning("suggest-sources: no auth token; returning empty matches")
        return []

    try:
        capabilities = await discovery.fetch_platform_capabilities(user_token)
        broker_body = _build_broker_payload(query, capabilities)
        agent_raw = await _execute_data_broker(token, broker_body)
        if not isinstance(agent_raw, dict):
            logger.warning("suggest-sources: broker returned non-object payload")
            return []
        matches = _parse_agent_matches(agent_raw)
        return [match.model_dump() for match in matches]
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "suggest-sources broker HTTP %s: %s",
            exc.response.status_code,
            exc.response.text[:160],
        )
        return []
    except httpx.TimeoutException:
        logger.warning("suggest-sources broker timed out after %ss", _BROKER_TIMEOUT_S)
        return []
    except httpx.RequestError as exc:
        logger.warning("suggest-sources broker unreachable: %s", exc)
        return []
    except Exception as exc:
        logger.warning("suggest-sources broker error: %s", exc)
        return []
