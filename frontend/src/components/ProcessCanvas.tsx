import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  SelectionMode,
  useNodesState,
  useEdgesState,
  getNodesBounds,
  type Node,
  type Edge,
  type OnNodeDrag,
  type OnSelectionChangeFunc,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  createGroup,
  getProcessGraph,
  updateNodePosition,
  type ProcessGraph,
} from "../api";
import BpmnNode, { categoryOf, type BpmnNodeData } from "./BpmnNode";
import GroupOverlay from "./GroupOverlay";

const nodeTypes = { bpmn: BpmnNode, groupOverlay: GroupOverlay };
const SAVE_DEBOUNCE_MS = 250;
const GROUP_PAD = 20;

type SaveState = "idle" | "saving" | "saved" | "error";

function toFlow(graph: ProcessGraph): { nodes: Node[]; edges: Edge[] } {
  const laneLabel = new Map(
    graph.lanes.map((l) => [l.id, l.label ?? l.source_ref])
  );

  const overlayNodes: Node[] = (graph.groups ?? [])
    .filter((g) => g.bbox)
    .map((g) => ({
      id: `overlay:${g.id}`,
      type: "groupOverlay",
      position: { x: g.bbox!.x, y: g.bbox!.y },
      data: {
        width: g.bbox!.width,
        height: g.bbox!.height,
        label: `Agentic underlay · ${g.deployment_status}`,
      },
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
      zIndex: 0,
    }));

  const bpmnNodes: Node[] = graph.nodes.map((n) => ({
    id: n.id,
    type: "bpmn",
    position: { x: n.x, y: n.y },
    zIndex: 1,
    data: {
      label: n.label ?? n.source_ref,
      bpmnType: n.type,
      category: categoryOf(n.type),
      lane: n.lane_id ? laneLabel.get(n.lane_id) ?? null : null,
      groupId: n.group_id,
    } satisfies BpmnNodeData,
  }));

  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.id,
    source: e.source_node_id,
    target: e.target_node_id,
    label: e.label ?? undefined,
    deletable: false,
    animated: false,
    zIndex: 1,
  }));

  return { nodes: [...overlayNodes, ...bpmnNodes], edges };
}

interface Props {
  processId: string;
  onReset: () => void;
}

export default function ProcessCanvas({ processId, onReset }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const [meta, setMeta] = useState<ProcessGraph["process"] | null>(null);
  const [counts, setCounts] = useState({ nodes: 0, edges: 0, lanes: 0, groups: 0 });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [groupBusy, setGroupBusy] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);

  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const loadGraph = useCallback(async () => {
    const graph = await getProcessGraph(processId);
    const flow = toFlow(graph);
    setNodes(flow.nodes);
    setEdges(flow.edges);
    setMeta(graph.process);
    setCounts({
      nodes: graph.nodes.length,
      edges: graph.edges.length,
      lanes: graph.lanes.length,
      groups: graph.groups?.length ?? 0,
    });
    setSelectedIds([]);
  }, [processId, setNodes, setEdges]);

  useEffect(() => {
    let cancelled = false;
    loadGraph().catch((e) => !cancelled && setLoadError(e.message));
    return () => {
      cancelled = true;
    };
  }, [loadGraph]);

  const onSelectionChange: OnSelectionChangeFunc = useCallback(({ nodes: sel }) => {
    setSelectedIds(sel.filter((n) => n.type === "bpmn").map((n) => n.id));
  }, []);

  const onNodeDragStop: OnNodeDrag<Node> = useCallback(
    (_evt, node) => {
      if (node.type !== "bpmn") return;
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

  const handleCreateGroup = useCallback(async () => {
    if (selectedIds.length === 0) return;
    setGroupBusy(true);
    setGroupError(null);
    try {
      const selectedNodes = nodes.filter(
        (n) => n.type === "bpmn" && selectedIds.includes(n.id)
      );
      const bounds = getNodesBounds(selectedNodes);
      const bbox = {
        x: bounds.x - GROUP_PAD,
        y: bounds.y - GROUP_PAD,
        width: bounds.width + GROUP_PAD * 2,
        height: bounds.height + GROUP_PAD * 2,
      };
      await createGroup(processId, selectedIds, bbox);
      setSelectMode(false);
      await loadGraph();
    } catch (e) {
      setGroupError(e instanceof Error ? e.message : String(e));
    } finally {
      setGroupBusy(false);
    }
  }, [selectedIds, nodes, processId, loadGraph]);

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
        <button
          className={`btn${selectMode ? " btn--active" : ""}`}
          onClick={() => setSelectMode((v) => !v)}
          title="Drag a box on the canvas to select nodes"
        >
          {selectMode ? "Box select (on)" : "Box select"}
        </button>
        <button
          className="btn btn--accent"
          disabled={selectedIds.length === 0 || groupBusy}
          onClick={() => void handleCreateGroup()}
        >
          {groupBusy
            ? "Creating…"
            : `Create agentic group (${selectedIds.length})`}
        </button>
        <span className="canvas-meta">
          {meta?.filename} · {counts.nodes} nodes · {counts.edges} edges ·{" "}
          {counts.lanes} lanes · {counts.groups} groups
        </span>
        <span className={`save-pill save-pill--${saveState}`}>{saveLabel}</span>
      </div>
      {groupError && <div className="alert alert--error">{groupError}</div>}
      {selectMode && (
        <p className="canvas-hint">
          Drag a rectangle over nodes to select them, then click Create agentic group.
          Selection may span lanes.
        </p>
      )}
      <div className="canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          onSelectionChange={onSelectionChange}
          selectionOnDrag={selectMode}
          panOnDrag={!selectMode}
          selectionMode={SelectionMode.Partial}
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
