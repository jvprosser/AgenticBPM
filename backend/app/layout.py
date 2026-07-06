"""The "dumb cascade" fallback layout (BLUEPRINT §Step 2).

When a BPMN file has no Diagram Interchange coordinates, assign deterministic,
non-overlapping X/Y by layering nodes along the sequence flows (longest-path level =
column) and staggering rows. Every node lands on a unique ``(level, row)`` cell, so
coordinates are guaranteed distinct — enough for the React canvas to render.
"""

from __future__ import annotations

from collections import deque

from .parser import ParsedNode, ParsedEdge

COL_W = 240.0
ROW_H = 130.0
MARGIN_X = 80.0
MARGIN_Y = 80.0
STAGGER_Y = 30.0


def apply_cascade_layout(nodes: list[ParsedNode], edges: list[ParsedEdge]) -> None:
    """Mutate ``nodes`` in place, setting ``x``/``y`` via a layered cascade."""
    if not nodes:
        return

    ids = [n.source_ref for n in nodes]
    id_set = set(ids)

    adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for e in edges:
        if e.source_node_ref in id_set and e.target_node_ref in id_set:
            adj[e.source_node_ref].append(e.target_node_ref)
            indeg[e.target_node_ref] += 1

    # Longest-path leveling over the DAG portion (Kahn's algorithm).
    level: dict[str, int] = {i: 0 for i in ids}
    work_indeg = dict(indeg)
    queue = deque(i for i in ids if work_indeg[i] == 0)
    processed: set[str] = set()
    while queue:
        cur = queue.popleft()
        processed.add(cur)
        for nxt in adj[cur]:
            level[nxt] = max(level[nxt], level[cur] + 1)
            work_indeg[nxt] -= 1
            if work_indeg[nxt] == 0:
                queue.append(nxt)

    # Nodes stuck in cycles: level them relative to any processed predecessor.
    for i in ids:
        if i not in processed:
            preds = [e.source_node_ref for e in edges if e.target_node_ref == i]
            processed_preds = [level[p] for p in preds if p in processed]
            if processed_preds:
                level[i] = max(processed_preds) + 1

    # Assign a stable row within each level (sorted for determinism).
    by_level: dict[int, list[str]] = {}
    for i in sorted(ids):
        by_level.setdefault(level[i], []).append(i)

    pos: dict[str, tuple[float, float]] = {}
    for lvl, members in by_level.items():
        for row, node_id in enumerate(members):
            x = MARGIN_X + lvl * COL_W
            y = MARGIN_Y + row * ROW_H + (lvl % 2) * STAGGER_Y
            pos[node_id] = (x, y)

    for n in nodes:
        n.x, n.y = pos[n.source_ref]
