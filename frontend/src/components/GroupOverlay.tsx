import type { NodeProps } from "@xyflow/react";

export interface GroupOverlayData {
  width: number;
  height: number;
  label: string;
  [key: string]: unknown;
}

export default function GroupOverlayNode({ data }: NodeProps) {
  const d = data as GroupOverlayData;
  return (
    <div
      className="group-overlay"
      style={{ width: d.width, height: d.height }}
    >
      <span className="group-overlay__label">{d.label}</span>
    </div>
  );
}
