import type { GraphGroup, GraphNode, NodeTaskMetadata, ProcessGraph } from "../api";

/** Legacy duration telemetry removed from node metadata; leverage uses deployed zones only. */
export function nodeLaborHours(_meta: NodeTaskMetadata): number {
  return 0;
}

export function groupMemberIds(group: GraphGroup, nodes: GraphNode[]): string[] {
  if (group.node_ids?.length) return group.node_ids;
  return nodes.filter((n) => n.group_id === group.id).map((n) => n.id);
}

/** 1.0x base + 1.5x per 10 labor-hours covered by deployed assistant zones. */
export function computeHumanLeverageMultiplier(graph: ProcessGraph): number {
  let deployedLaborHours = 0;
  for (const group of graph.groups ?? []) {
    if (group.deployment_status !== "deployed") continue;
    const memberIds = groupMemberIds(group, graph.nodes);
    for (const nodeId of memberIds) {
      const node = graph.nodes.find((n) => n.id === nodeId);
      if (node) deployedLaborHours += nodeLaborHours(node.metadata);
    }
  }
  return 1.0 + (deployedLaborHours / 10) * 1.5;
}

export function formatLeverageMultiplier(multiplier: number): string {
  return `${multiplier.toFixed(1)}x Augmented Capacity`;
}
