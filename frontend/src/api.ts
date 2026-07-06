export interface UploadResult {
  upload_id: string;
  filename: string;
  size_bytes: number;
  stored_path: string;
  received_at: string;
  process_id: string;
  process_name: string | null;
  counts: { nodes: number; edges: number; lanes: number };
  layout_source: string;
}

export interface GraphNode {
  id: string;
  source_ref: string;
  type: string;
  label: string | null;
  x: number;
  y: number;
  lane_id: string | null;
  parent_ref: string | null;
  attached_to_ref: string | null;
}

export interface GraphEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  label: string | null;
}

export interface GraphLane {
  id: string;
  source_ref: string;
  label: string | null;
}

export interface ProcessGraph {
  process: { id: string; filename: string; format: string; created_at: string };
  lanes: GraphLane[];
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface HealthResult {
  status: string;
  service: string;
  version: string;
}

export async function getHealth(): Promise<HealthResult> {
  const res = await fetch("/health");
  if (!res.ok) throw new Error(`Health check failed (${res.status})`);
  return res.json();
}

async function parseError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (body?.detail) return body.detail;
  } catch {
    /* non-JSON error body */
  }
  return `${fallback} (${res.status})`;
}

export async function uploadProcessFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseError(res, "Upload failed"));
  return res.json();
}

export async function getProcessGraph(processId: string): Promise<ProcessGraph> {
  const res = await fetch(`/api/processes/${processId}`);
  if (!res.ok) throw new Error(await parseError(res, "Failed to load process"));
  return res.json();
}

export async function updateNodePosition(
  processId: string,
  nodeId: string,
  x: number,
  y: number
): Promise<void> {
  const res = await fetch(
    `/api/processes/${processId}/nodes/${encodeURIComponent(nodeId)}/position`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y }),
    }
  );
  if (!res.ok) throw new Error(await parseError(res, "Failed to save position"));
}
