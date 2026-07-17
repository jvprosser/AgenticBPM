"""Namespace-tolerant BPMN 2.0 parser.

Per BLUEPRINT §Step 2: match on *local* element names (not fixed OMG namespace URIs),
because sample files such as ``docs/Claims_process.xml`` use placeholder namespaces.
Extracts semantic nodes, edges, lanes, and nested subprocess children, and reads
Diagram Interchange (``bpmndi:BPMNShape``) coordinates when present.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

# Local tag names treated as flow nodes.
_EVENTS = {
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "boundaryEvent",
}
_TASKS = {
    "task",
    "userTask",
    "serviceTask",
    "scriptTask",
    "businessRuleTask",
    "manualTask",
    "sendTask",
    "receiveTask",
    "callActivity",
}
_GATEWAYS = {
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "complexGateway",
    "eventBasedGateway",
}
_SUBPROCESS = {"subProcess", "transaction", "adHocSubProcess"}
FLOW_NODE_TYPES = _EVENTS | _TASKS | _GATEWAYS | _SUBPROCESS


@dataclass
class ParsedNode:
    source_ref: str
    type: str
    label: Optional[str] = None
    lane_ref: Optional[str] = None
    parent_ref: Optional[str] = None
    attached_to_ref: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None


@dataclass
class ParsedEdge:
    source_ref: str
    source_node_ref: str
    target_node_ref: str
    label: Optional[str] = None


@dataclass
class ParsedLane:
    source_ref: str
    label: Optional[str] = None


@dataclass
class ParsedProcess:
    process_name: Optional[str]
    nodes: list[ParsedNode] = field(default_factory=list)
    edges: list[ParsedEdge] = field(default_factory=list)
    lanes: list[ParsedLane] = field(default_factory=list)
    has_di: bool = False


def _local(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _filename_stem(filename: str) -> str:
    """Return filename without extension, or a safe default."""
    base = (filename or "").strip()
    if not base:
        return "Untitled Process"
    if "." in base:
        return base.rsplit(".", 1)[0].strip() or "Untitled Process"
    return base


def extract_process_name(raw_xml: str, filename: str) -> str:
    """Extract the BPMN ``process`` element ``name`` attribute, namespace-agnostic.

    Falls back to the sanitized upload filename when ``name`` is missing or blank.
    """
    fallback = _filename_stem(filename)
    if not raw_xml or not raw_xml.strip():
        return fallback
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return fallback

    for el in root.iter():
        if _local(el.tag) != "process":
            continue
        name = (el.attrib.get("name") or "").strip()
        if name:
            return name
        break

    return fallback


def _extract_di_coords(root: ET.Element) -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    for el in root.iter():
        if _local(el.tag) != "BPMNShape":
            continue
        ref = el.attrib.get("bpmnElement")
        if not ref:
            continue
        for child in el:
            if _local(child.tag) == "Bounds":
                try:
                    coords[ref] = (float(child.attrib["x"]), float(child.attrib["y"]))
                except (KeyError, ValueError):
                    pass
                break
    return coords


def _extract_lanes(root: ET.Element) -> tuple[list[ParsedLane], dict[str, str]]:
    lanes: list[ParsedLane] = []
    lane_of: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) != "lane":
            continue
        lane_id = el.attrib.get("id")
        if not lane_id:
            continue
        lanes.append(ParsedLane(source_ref=lane_id, label=el.attrib.get("name")))
        for child in el:
            if _local(child.tag) == "flowNodeRef" and child.text:
                lane_of[child.text.strip()] = lane_id
    return lanes, lane_of


def parse_bpmn(xml_text: str) -> ParsedProcess:
    root = ET.fromstring(xml_text)

    coords = _extract_di_coords(root)
    lanes, lane_of = _extract_lanes(root)

    nodes: list[ParsedNode] = []
    edges: list[ParsedEdge] = []
    process_name: Optional[str] = None

    def add_node(el: ET.Element, tag: str, parent_subprocess: Optional[str]) -> None:
        ref = el.attrib.get("id")
        if not ref:
            return
        xy = coords.get(ref, (None, None))
        nodes.append(
            ParsedNode(
                source_ref=ref,
                type=tag,
                label=el.attrib.get("name"),
                lane_ref=lane_of.get(ref),
                parent_ref=parent_subprocess,
                attached_to_ref=el.attrib.get("attachedToRef"),
                x=xy[0],
                y=xy[1],
            )
        )

    def walk(el: ET.Element, parent_subprocess: Optional[str]) -> None:
        nonlocal process_name
        for child in el:
            tag = _local(child.tag)
            cid = child.attrib.get("id")
            if tag == "process":
                if process_name is None:
                    process_name = child.attrib.get("name") or child.attrib.get("id")
                walk(child, None)
            elif tag in _SUBPROCESS:
                add_node(child, tag, parent_subprocess)
                walk(child, cid)
            elif tag in FLOW_NODE_TYPES:
                add_node(child, tag, parent_subprocess)
                walk(child, parent_subprocess)
            elif tag == "sequenceFlow":
                src = child.attrib.get("sourceRef")
                tgt = child.attrib.get("targetRef")
                if src and tgt and cid:
                    edges.append(
                        ParsedEdge(
                            source_ref=cid,
                            source_node_ref=src,
                            target_node_ref=tgt,
                            label=child.attrib.get("name"),
                        )
                    )
            else:
                walk(child, parent_subprocess)

    walk(root, None)

    has_di = len(coords) > 0 and all(n.x is not None for n in nodes)
    return ParsedProcess(
        process_name=process_name,
        nodes=nodes,
        edges=edges,
        lanes=lanes,
        has_di=has_di,
    )
