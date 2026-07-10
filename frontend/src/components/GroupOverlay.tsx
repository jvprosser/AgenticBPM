import type { NodeProps } from "@xyflow/react";

export interface GroupOverlayData {
  width: number;
  height: number;
  label: string;
  isProposed?: boolean;
  groupId?: string;
  [key: string]: unknown;
}

export default function GroupOverlayNode({ data }: NodeProps) {
  const d = data as GroupOverlayData;
  const className = d.isProposed
    ? "group-overlay group-overlay--proposed"
    : "group-overlay";
  const labelClass = d.isProposed
    ? "group-overlay__label group-overlay__label--pulse"
    : "group-overlay__label";

  return (
    <div className={className} style={{ width: d.width, height: d.height }}>
      <span className={labelClass}>{d.label}</span>
    </div>
  );
}
