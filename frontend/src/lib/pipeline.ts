import type { AggregatedPipeline, GraphNode, ProcessGraph } from "../api";

export function aggregatePipelineFromGraph(
  graph: ProcessGraph,
  memberIds: string[]
): AggregatedPipeline {
  const scope_tasks: AggregatedPipeline["scope_tasks"] = [];
  const sourcesMap = new Map<string, string[]>();
  const output_products: string[] = [];
  const seenOutputs = new Set<string>();

  for (const nodeId of memberIds) {
    const node = graph.nodes.find((n) => n.id === nodeId);
    if (!node) continue;
    scope_tasks.push({
      id: node.id,
      label: node.label ?? node.source_ref,
    });
    harvestNodeMetadata(node, sourcesMap, output_products, seenOutputs);
  }

  const data_sources = [...sourcesMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([source_name, human_procedures]) => ({ source_name, human_procedures }));

  return { scope_tasks, data_sources, output_products };
}

function harvestNodeMetadata(
  node: GraphNode,
  sourcesMap: Map<string, string[]>,
  output_products: string[],
  seenOutputs: Set<string>
) {
  for (const entry of node.metadata.data_sources ?? []) {
    const sourceName = entry.source_name.trim();
    const procedure = entry.human_procedure.trim();
    if (!sourceName && !procedure) continue;
    const key = sourceName || "(unnamed source)";
    const bucket = sourcesMap.get(key) ?? [];
    if (procedure && !bucket.includes(procedure)) {
      bucket.push(procedure);
    }
    sourcesMap.set(key, bucket);
  }
  const output = node.metadata.output_end_product.trim();
  if (output && !seenOutputs.has(output)) {
    seenOutputs.add(output);
    output_products.push(output);
  }
}
