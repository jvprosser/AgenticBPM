export interface ProcessSummary {
  id: string;
  process_name: string;
  filename: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  leverage_multiplier: number;
  node_count: number;
}

export interface ProcessListResponse {
  processes: ProcessSummary[];
}

export interface ProcessPatchBody {
  process_name?: string;
  description?: string | null;
}

export interface UploadResult {
  id: string;
  upload_id?: string;
  filename: string;
  size_bytes?: number;
  stored_path?: string | null;
  received_at?: string;
  process_id: string;
  process_name: string;
  counts: { nodes: number; edges: number; lanes: number };
  layout_source: string;
  created_at?: string;
  updated_at?: string;
}

export interface DataSourceProcedure {
  subtask_id?: string;
  source_name: string;
  user_procedure: string;
  data_destinations: string;
  is_intermediate: boolean;
  execution_mode?: "agent_automated" | "user_manual" | string;
  agent_endpoint_key?: string;
  input_parameter_mappings?: Record<string, string>;
  artifact_path_pattern?: string;
  qualified_name?: string;
  destination?: string;
}

export interface NodeTaskMetadata {
  input_parameter: string;
  data_sources: DataSourceProcedure[];
  output_end_product: string;
  final_activity: string;
  user_validation_required: boolean;
}

export interface GroupMetadataRecord {
  name: string | null;
  owner: string | null;
  description: string | null;
}

export const EMPTY_NODE_TASK_METADATA: NodeTaskMetadata = {
  input_parameter: "",
  data_sources: [],
  output_end_product: "",
  final_activity: "",
  user_validation_required: false,
};

export const EMPTY_GROUP_METADATA: GroupMetadataRecord = {
  name: null,
  owner: null,
  description: null,
};

/** @deprecated Legacy alias — node metadata uses {@link NodeTaskMetadata}. */
export type MetadataRecord = NodeTaskMetadata | GroupMetadataRecord;

export function isNodeTaskMetadata(
  meta: NodeTaskMetadata | GroupMetadataRecord
): meta is NodeTaskMetadata {
  return "data_sources" in meta;
}

export interface GraphNode {
  id: string;
  source_ref: string;
  type: string;
  label: string | null;
  x: number;
  y: number;
  lane_id: string | null;
  group_id: string | null;
  parent_ref: string | null;
  attached_to_ref: string | null;
  metadata: NodeTaskMetadata;
}

export interface BboxGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AggregatedPipelineTask {
  id: string;
  label: string;
}

export interface AggregatedPipelineSource {
  source_name: string;
  human_procedures: string[];
}

export interface AggregatedPipeline {
  scope_tasks: AggregatedPipelineTask[];
  data_sources: AggregatedPipelineSource[];
  output_products: string[];
}

export const EMPTY_AGGREGATED_PIPELINE: AggregatedPipeline = {
  scope_tasks: [],
  data_sources: [],
  output_products: [],
};

export interface GraphGroup {
  id: string;
  bbox: BboxGeometry | null;
  deployment_status: string;
  metadata: GroupMetadataRecord;
  node_ids?: string[];
  workflow_definition?: SuggestWorkflow | null;
  aggregated_pipeline?: AggregatedPipeline;
}

export interface SuggestAgent {
  name: string;
  role: string;
  goal: string;
  backstory: string;
  tools: string[];
}

export interface SuggestTask {
  description: string;
  agent: string;
}

export interface SuggestWorkflow {
  workflow_name: string;
  type: "task" | "conversational";
  manager_agent: boolean;
  planning: boolean;
  agents: SuggestAgent[];
  tasks: SuggestTask[];
  confidence: number;
  rationale: string;
}

export interface SuggestResult extends SuggestWorkflow {
  discovery_active: boolean;
  group_id: string;
  node_ids: string[];
  bbox: BboxGeometry;
  deployment_status: string;
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
  process: {
    id: string;
    process_name: string;
    filename: string;
    description?: string | null;
    created_at: string;
    updated_at: string;
  };
  lanes: GraphLane[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
}

export interface CreateGroupResult {
  id: string;
  process_id: string;
  node_ids: string[];
  bbox: BboxGeometry;
  deployment_status: string;
}

export interface HealthResult {
  status: string;
  service: string;
  version: string;
}

export interface DiscoveryNamedEntry {
  name: string;
  description: string;
}

export interface DiscoveryMcpServer extends DiscoveryNamedEntry {
  tools: DiscoveryNamedEntry[];
}

export interface DiscoveryResult {
  models: string[];
  default_model?: string | null;
  mcp_servers: DiscoveryMcpServer[];
  tools: DiscoveryNamedEntry[];
  discovery_active: boolean;
  source: string;
  degraded_reason?: string | null;
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

export async function getDiscovery(): Promise<DiscoveryResult> {
  const res = await fetch("/api/discovery", { credentials: "include" });
  if (!res.ok) throw new Error(await parseError(res, "Discovery failed"));
  return res.json();
}

export async function listProcesses(): Promise<ProcessListResponse> {
  const res = await fetch("/api/processes");
  if (!res.ok) throw new Error(await parseError(res, "Failed to load process registry"));
  return res.json();
}

export async function patchProcess(
  processId: string,
  body: ProcessPatchBody
): Promise<ProcessSummary> {
  const res = await fetch(`/api/processes/${processId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to update process"));
  return res.json();
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

export async function createGroup(
  processId: string,
  nodeIds: string[],
  bbox?: BboxGeometry
): Promise<CreateGroupResult> {
  const res = await fetch(`/api/processes/${processId}/groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_ids: nodeIds, bbox: bbox ?? null }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to create group"));
  return res.json();
}

export interface MetadataUpsertBody {
  owner_type: "node" | "group";
  owner_id: string;
  metadata: NodeTaskMetadata | GroupMetadataRecord;
}

export async function suggestOptimization(processId: string): Promise<SuggestResult> {
  const res = await fetch(`/api/processes/${processId}/suggest`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await parseError(res, "Suggestion failed"));
  return res.json();
}

export interface StrategicOverrideResult {
  id: string;
  process_id: string;
  node_ids: string[];
  created_at: string;
  purged_proposed_groups: string[];
}

export async function createStrategicOverride(
  processId: string,
  nodeIds: string[]
): Promise<StrategicOverrideResult> {
  const res = await fetch(`/api/processes/${processId}/overrides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ node_ids: nodeIds }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Override failed"));
  return res.json();
}

export async function upsertMetadata(
  processId: string,
  body: MetadataUpsertBody
): Promise<{
  owner_type: string;
  owner_id: string;
  metadata: NodeTaskMetadata | GroupMetadataRecord;
}> {
  const res = await fetch(`/api/processes/${processId}/metadata`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to save metadata"));
  return res.json();
}

export type ClaimInstanceStatus =
  | "INITIATED"
  | "PROCESSING"
  | "AWAITING_USER_VALIDATION"
  | "COMPLETED"
  | "FAILED";

export type SubtaskExecutionStatus =
  | "PENDING"
  | "RUNNING"
  | "AWAITING_USER_VALIDATION"
  | "APPROVED"
  | "FAILED";

export interface ClaimInstanceRecord {
  id: string;
  claim_number: string;
  process_id: string;
  claim_parameters: Record<string, unknown>;
  status: ClaimInstanceStatus;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SubtaskExecutionRecord {
  id: string;
  claim_instance_id: string;
  subtask_id: string;
  subtask_name?: string | null;
  status: SubtaskExecutionStatus;
  trace_id?: string | null;
  session_id?: string | null;
  artifact_path?: string | null;
  output_payload?: Record<string, unknown> | null;
  validation_feedback?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ClaimDetailResponse {
  claim: ClaimInstanceRecord;
  subtask_executions: SubtaskExecutionRecord[];
}

export async function listProcessClaims(
  processId: string,
  nodeId: string
): Promise<ClaimInstanceRecord[]> {
  const params = new URLSearchParams({ node_id: nodeId });
  const res = await fetch(`/api/processes/${processId}/claims?${params}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to load claim runs"));
  const body = (await res.json()) as { claims: ClaimInstanceRecord[] };
  return body.claims;
}

export async function getClaimDetail(claimId: string): Promise<ClaimDetailResponse> {
  const res = await fetch(`/api/claims/${claimId}`, { credentials: "include" });
  if (!res.ok) throw new Error(await parseError(res, "Failed to load claim detail"));
  return res.json();
}

export async function runClaim(body: {
  process_id: string;
  target_node_id: string;
  claim_number: string;
  claim_parameters: Record<string, string>;
}): Promise<ClaimDetailResponse> {
  const res = await fetch("/api/claims/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to start claim run"));
  return res.json();
}

export async function fetchExecutionArtifact(executionId: string): Promise<unknown> {
  const res = await fetch(`/api/artifacts/${executionId}`, { credentials: "include" });
  if (!res.ok) throw new Error(await parseError(res, "Failed to load artifact"));
  const body = (await res.json()) as { artifact: unknown };
  return body.artifact;
}

export async function approveSubtaskExecution(
  executionId: string,
  validationFeedback: string
): Promise<ClaimDetailResponse> {
  const res = await fetch(`/api/subtasks/${executionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ validation_feedback: validationFeedback }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to approve subtask"));
  return res.json();
}

export async function rejectSubtaskExecution(
  executionId: string,
  validationFeedback: string
): Promise<ClaimDetailResponse> {
  const res = await fetch(`/api/subtasks/${executionId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ validation_feedback: validationFeedback }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to reject subtask"));
  return res.json();
}
