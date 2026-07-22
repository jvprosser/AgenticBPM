import type { DataSourceProcedure, NodeTaskMetadata } from "../api";

export interface StructuredTextObject {
  objective?: string;
  steps?: string[];
  key_considerations?: string[];
}

export interface AugmentedDataSource {
  subtask_id?: string;
  source_name?: string;
  user_procedure?: string | StructuredTextObject;
  human_procedure?: string | StructuredTextObject;
  data_destinations?: string;
  is_intermediate?: boolean;
  execution_mode?: string;
  agent_endpoint_key?: string;
  input_parameter_mappings?: Record<string, string>;
  artifact_path_pattern?: string;
  qualified_name?: string;
  destination?: string;
  business_terms?: string | string[];
  classifications?: string | string[] | Record<string, unknown>;
  asset_type?: string;
  owner?: string;
  description?: string;
}

export interface AIDelegationPayload {
  process_instance_id?: string;
  target_node_id?: string;
  input_parameter?: string;
  final_activity?: string | StructuredTextObject;
  finalized_artifact?: string;
  output_end_product?: string;
  user_validation_required?: boolean;
  data_sources?: AugmentedDataSource[];
  subtasks?: AugmentedDataSource[];
}

export interface DelegationPollResult {
  enriched_json?: unknown;
  agent_output?: unknown;
  output?: unknown;
  final_result?: unknown;
  workflow_events?: unknown[];
  events?: unknown[];
  metadata?: unknown;
}

const BLOCKED_NESTED_KEYS = new Set([
  "inputs",
  "input",
  "payload",
  "metadata",
  "context",
]);

const AGENT_OUTPUT_NESTED_KEYS = [
  "agent_output",
  "enriched_json",
  "enrichedJson",
  "output",
  "result",
  "content",
  "message",
  "data",
  "answer",
  "final_answer",
  "finalAnswer",
  "final_result",
];

function unescapeNewlines(text: string): string {
  return text.replace(/\\n/g, "\n");
}

function tryParseJson(text: string): unknown | null {
  try {
    return JSON.parse(text.trim());
  } catch {
    return null;
  }
}

export function extractJsonFromText(text: string): unknown | null {
  const normalized = unescapeNewlines(text);

  for (const match of normalized.matchAll(/```(?:json)?\s*\n?([\s\S]*?)```/gi)) {
    const parsed = tryParseJson(match[1]);
    if (parsed !== null) return parsed;
  }

  const braceStart = normalized.indexOf("{");
  const braceEnd = normalized.lastIndexOf("}");
  if (braceStart >= 0 && braceEnd > braceStart) {
    const parsed = tryParseJson(normalized.slice(braceStart, braceEnd + 1));
    if (parsed !== null) return parsed;
  }

  return tryParseJson(normalized);
}

export function isStructuredTextObject(value: unknown): value is StructuredTextObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const obj = value as StructuredTextObject;
  return Boolean(obj.objective || obj.steps?.length || obj.key_considerations?.length);
}

export function formatStructuredText(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (isStructuredTextObject(value)) {
    const parts: string[] = [];
    if (value.objective) {
      parts.push(`Objective:\n${value.objective}`);
    }
    const steps = Array.isArray(value.steps) ? value.steps : [];
    if (steps.length > 0) {
      parts.push(`\n\nSteps:\n${steps.map((step) => `• ${step}`).join("\n")}`);
    }
    const considerations = Array.isArray(value.key_considerations)
      ? value.key_considerations
      : [];
    if (considerations.length > 0) {
      parts.push(
        `\n\nKey Considerations:\n${considerations.map((item) => `• ${item}`).join("\n")}`
      );
    }
    return parts.join("");
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

export function formatCatalogValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function hasCatalogMetadata(
  source: Partial<DataSourceProcedure> | AugmentedDataSource
): boolean {
  return Boolean(
    source.qualified_name ||
      source.business_terms ||
      source.classifications ||
      source.asset_type ||
      source.owner ||
      source.description ||
      source.destination
  );
}

function coercePayload(value: unknown): AIDelegationPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const obj = value as Record<string, unknown>;
  const sources = obj.data_sources ?? obj.subtasks;
  if (
    obj.input_parameter !== undefined ||
    obj.final_activity !== undefined ||
    obj.finalized_artifact !== undefined ||
    obj.output_end_product !== undefined ||
    obj.user_validation_required !== undefined ||
    Array.isArray(sources)
  ) {
    return obj as AIDelegationPayload;
  }
  return null;
}

export function isAugmentedPayload(value: unknown): value is AIDelegationPayload {
  const payload = coercePayload(value);
  if (!payload) return false;

  if (isStructuredTextObject(payload.final_activity)) return true;

  const sources = payload.data_sources ?? payload.subtasks ?? [];
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    const procedure = source.user_procedure ?? source.human_procedure;
    if (isStructuredTextObject(procedure)) return true;
    if (hasCatalogMetadata(source)) return true;
  }

  return false;
}

function resolveCandidate(candidate: unknown, depth = 0): AIDelegationPayload | null {
  if (candidate === undefined || candidate === null || depth > 8) return null;

  if (typeof candidate === "string") {
    const parsed = extractJsonFromText(candidate);
    return resolveCandidate(parsed, depth + 1);
  }

  const direct = coercePayload(candidate);
  if (direct && isAugmentedPayload(direct)) return direct;

  if (typeof candidate !== "object") return null;
  if (Array.isArray(candidate)) {
    for (let index = candidate.length - 1; index >= 0; index -= 1) {
      const nested = resolveCandidate(candidate[index], depth + 1);
      if (nested) return nested;
    }
    return null;
  }

  const obj = candidate as Record<string, unknown>;
  for (const key of AGENT_OUTPUT_NESTED_KEYS) {
    if (!(key in obj)) continue;
    const nested = resolveCandidate(obj[key], depth + 1);
    if (nested) return nested;
  }

  for (const [key, nested] of Object.entries(obj)) {
    if (BLOCKED_NESTED_KEYS.has(key)) continue;
    const resolved = resolveCandidate(nested, depth + 1);
    if (resolved) return resolved;
  }

  return null;
}

function extractFromEventStream(events: unknown[]): AIDelegationPayload | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const resolved = resolveCandidate(events[index]);
    if (resolved) return resolved;
  }
  return null;
}

export function parseAugmentedPayload(rawResponse: DelegationPollResult): AIDelegationPayload | null {
  const topLevelCandidates = [
    rawResponse.agent_output,
    rawResponse.enriched_json,
    rawResponse.output,
    rawResponse.final_result,
  ];

  for (const candidate of topLevelCandidates) {
    const augmented = resolveCandidate(candidate);
    if (augmented) {
      console.log("🤖 [DELEGATION RESULT] Agent Output Payload:", augmented);
      return augmented;
    }
  }

  const eventStream = rawResponse.workflow_events ?? rawResponse.events;
  if (Array.isArray(eventStream)) {
    const fromEvents = extractFromEventStream(eventStream);
    if (fromEvents) {
      console.log("🤖 [DELEGATION RESULT] Agent Output Payload:", fromEvents);
      return fromEvents;
    }
  }

  return null;
}

function normalizeAugmentedSource(source: AugmentedDataSource): DataSourceProcedure {
  const procedureRaw = source.user_procedure ?? source.human_procedure;
  const executionMode =
    source.execution_mode === "user_manual" || source.execution_mode === "agent_automated"
      ? source.execution_mode
      : "agent_automated";

  return {
    subtask_id: source.subtask_id ?? "",
    source_name: source.source_name ?? "",
    user_procedure: formatStructuredText(procedureRaw),
    data_destinations: source.data_destinations ?? "",
    is_intermediate: source.is_intermediate ?? false,
    execution_mode: executionMode,
    agent_endpoint_key: source.agent_endpoint_key ?? "",
    input_parameter_mappings: source.input_parameter_mappings ?? {},
    artifact_path_pattern: source.artifact_path_pattern ?? "",
    qualified_name: source.qualified_name ?? "",
    destination: source.destination ?? "",
    business_terms: source.business_terms,
    classifications: source.classifications,
    asset_type: source.asset_type ?? "",
    owner: source.owner ?? "",
    description: source.description ?? "",
  };
}

export function payloadToNodeMetadata(payload: AIDelegationPayload): NodeTaskMetadata {
  const rawSources = payload.data_sources ?? payload.subtasks ?? [];
  const data_sources = rawSources.map((source) => normalizeAugmentedSource(source));

  return {
    input_parameter: payload.input_parameter ?? "",
    final_activity: formatStructuredText(payload.final_activity),
    output_end_product: payload.finalized_artifact ?? payload.output_end_product ?? "",
    user_validation_required: Boolean(payload.user_validation_required),
    data_sources,
  };
}
