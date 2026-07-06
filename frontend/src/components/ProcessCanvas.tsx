import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type OnNodeDrag,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getProcessGraph, updateNodePosition, type ProcessGraph } from "../api";
import BpmnNode, { categoryOf, type BpmnNodeData } from "./BpmnNode";

const nodeTypes = { bpmn: BpmnNode };
const SAVE_DEBOUNCE_MS = 250;

type SaveState = "idle" | "saving" | "saved" | "error";

function toFlow(graph: ProcessGraph): { nodes: Node[]; edges: Edge[] } {
  const laneLabel = new Map(
    graph.lanes.map((l) => [l.id, l.label ?? l.source_ref])
  );
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.id,
    type: "bpmn",
    position: { x: n.x, y: n.y },
    data: {
      label: n.label ?? n.source_ref,
      bpmnType: n.type,
      category: categoryOf(n.type),
      lane: n.lane_id ? laneLabel.get(n.lane_id) ?? null : null,
    } satisfies BpmnNodeData,
  }));
  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.id,
    source: e.source_node_id,
    target: e.target_node_id,
    label: e.label ?? undefined,
    deletable: false,
    animated: false,
  }));
  return { nodes, edges };
}

interface Props {
  processId: string;
  onReset: () => void;
}

export default function ProcessCanvas({ processId, onReset }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const [meta, setMeta] = useState<ProcessGraph["process"] | null>(null);
  const [counts, setCounts] = useState({ nodes: 0, edges: 0, lanes: 0 });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    let cancelled = false;
    getProcessGraph(processId)
      .then((graph) => {
        if (cancelled) return;
        const flow = toFlow(graph);
        setNodes(flow.nodes);
        setEdges(flow.edges);
        setMeta(graph.process);
        setCounts({
          nodes: graph.nodes.length,
          edges: graph.edges.length,
          lanes: graph.lanes.length,
        });
      })
      .catch((e) => !cancelled && setLoadError(e.message));
    return () => {
      cancelled = true;
    };
  }, [processId, setNodes, setEdges]);

  // Persist the final position on drag-end, debounced per node so rapid
  // successive drags collapse into a single SQLite write.
  const onNodeDragStop: OnNodeDrag<Node> = useCallback(
    (_evt, node) => {
      const existing = timers.current.get(node.id);
      if (existing) clearTimeout(existing);
      setSaveState("saving");
      const handle = setTimeout(() => {
        updateNodePosition(processId, node.id, node.position.x, node.position.y)
          .then(() => setSaveState("saved"))
          .catch(() => setSaveState("error"))
          .finally(() => timers.current.delete(node.id));
      }, SAVE_DEBOUNCE_MS);
      timers.current.set(node.id, handle);
    },
    [processId]
  );

  const saveLabel = useMemo(
    () =>
      ({
        idle: "",
        saving: "saving…",
        saved: "position saved",
        error: "save failed",
      })[saveState],
    [saveState]
  );

  if (loadError) {
    return <div className="alert alert--error">Failed to load graph: {loadError}</div>;
  }

  return (
    <div className="canvas-wrap">
      <div className="canvas-toolbar">
        <button className="btn" onClick={onReset}>
          ← New file
        </button>
        <span className="canvas-meta">
          {meta?.filename} · {counts.nodes} nodes · {counts.edges} edges ·{" "}
          {counts.lanes} lanes
        </span>
        <span className={`save-pill save-pill--${saveState}`}>{saveLabel}</span>
      </div>
      <div className="canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          nodesDraggable
          nodesConnectable={false}
          elementsSelectable
          deleteKeyCode={null}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
    </div>
  );
}
