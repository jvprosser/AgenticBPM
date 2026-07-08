import { Handle, Position, type NodeProps } from "@xyflow/react";

export type BpmnCategory = "event" | "task" | "gateway" | "subprocess";

export interface BpmnNodeData {
  label: string;
  bpmnType: string;
  category: BpmnCategory;
  lane: string | null;
  groupId: string | null;
  [key: string]: unknown;
}

export function categoryOf(bpmnType: string): BpmnCategory {
  const t = bpmnType.toLowerCase();
  if (t.includes("gateway")) return "gateway";
  if (t.includes("event")) return "event";
  if (t.includes("subprocess") || t === "transaction" || t.includes("adhoc"))
    return "subprocess";
  return "task";
}

const CATEGORY_COLOR: Record<BpmnCategory, string> = {
  event: "#2ea043",
  task: "#3b82f6",
  gateway: "#f59e0b",
  subprocess: "#a855f7",
};

export default function BpmnNode({ data, selected }: NodeProps) {
  const d = data as BpmnNodeData;
  const color = CATEGORY_COLOR[d.category];
  const isEvent = d.category === "event";
  const isGateway = d.category === "gateway";

  return (
    <div
      className={`bpmn-node bpmn-node--${d.category}${selected ? " is-selected" : ""}${
        d.groupId ? " bpmn-node--grouped" : ""
      }`}
      style={{
        borderColor: color,
        borderRadius: isEvent ? 999 : isGateway ? 6 : 8,
      }}
      title={`${d.bpmnType}${d.lane ? ` · ${d.lane}` : ""}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="bpmn-node__type" style={{ color }}>
        {d.bpmnType}
      </span>
      <span className="bpmn-node__label">{d.label || d.bpmnType}</span>
      {d.lane && <span className="bpmn-node__lane">{d.lane}</span>}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
