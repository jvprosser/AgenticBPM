import { useCallback, useEffect, useRef, useState } from "react";
import {
  upsertMetadata,
  EMPTY_AGGREGATED_PIPELINE,
  EMPTY_NODE_TASK_METADATA,
  isNodeTaskMetadata,
  type AggregatedPipeline,
  type DataSourceProcedure,
  type GroupMetadataRecord,
  type NodeTaskMetadata,
} from "../api";
import AssistantGroupPopover from "./AssistantGroupPopover";

const DEBOUNCE_MS = 400;
const TASK_EDITOR_WIDTH_PX = 600;

export interface MetadataTarget {
  ownerType: "node" | "group";
  ownerId: string;
  title: string;
  variant?: "default" | "proposed";
  groupNodeIds?: string[];
  workflow?: import("../api").SuggestWorkflow | null;
  aggregatedPipeline?: AggregatedPipeline;
}

interface Props {
  processId: string;
  target: MetadataTarget | null;
  initial: NodeTaskMetadata | GroupMetadataRecord;
  onClose: () => void;
  onSaved: (
    ownerType: "node" | "group",
    ownerId: string,
    meta: NodeTaskMetadata | GroupMetadataRecord
  ) => void;
  onRejectProposal?: (nodeIds: string[]) => Promise<void>;
}

type SaveState = "idle" | "saving" | "saved" | "error";

interface DelegatePlanningResult {
  ok?: boolean;
  status?: "running" | "completed" | "timeout" | "error";
  toast_message?: string;
  trace_id?: string;
  session_id?: string;
  session_directory?: string | null;
  final_result?: unknown;
  enriched_json?: unknown;
  local_artifact_path?: string | null;
  artifact_upload?: {
    success?: boolean;
    message?: string;
    file_path?: string;
  } | null;
  poll_completed?: boolean;
  poll_count?: number;
  gateway_message?: string;
  detail?: string;
  metadata?: NodeTaskMetadata;
}

interface DelegateDialogState {
  variant: "success" | "error" | "running";
  title: string;
  message: string;
  result?: DelegatePlanningResult;
}

const DELEGATE_POLL_INTERVAL_MS = 2000;
const DELEGATE_POLL_TIMEOUT_MS = 300_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatJsonBlock(value: unknown): string {
  if (value === undefined || value === null) {
    return "No data available.";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function normalizeNodeInitial(initial: NodeTaskMetadata | GroupMetadataRecord): NodeTaskMetadata {
  if (isNodeTaskMetadata(initial)) {
    return {
      data_sources: initial.data_sources.map((row) => ({
        source_name: row.source_name ?? "",
        human_procedure: row.human_procedure ?? "",
        data_destinations: row.data_destinations ?? "",
        is_intermediate: row.is_intermediate ?? false,
      })),
      output_end_product: initial.output_end_product ?? "",
      final_activity: initial.final_activity ?? "",
    };
  }
  return { ...EMPTY_NODE_TASK_METADATA };
}

export default function MetadataPopover({
  processId,
  target,
  initial,
  onClose,
  onSaved,
  onRejectProposal,
}: Props) {
  const [nodeForm, setNodeForm] = useState<NodeTaskMetadata>(normalizeNodeInitial(initial));
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [delegateBusy, setDelegateBusy] = useState(false);
  const [delegateDialog, setDelegateDialog] = useState<DelegateDialogState | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestNode = useRef(nodeForm);

  useEffect(() => {
    const nextNode = normalizeNodeInitial(initial);
    setNodeForm(nextNode);
    latestNode.current = nextNode;
    setSaveState("idle");
    setDelegateDialog(null);
  }, [target?.ownerId, target?.ownerType]);

  useEffect(() => {
    if (!delegateDialog) return;
    const onKey = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") setDelegateDialog(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [delegateDialog]);

  const persist = useCallback(
    async (payload: NodeTaskMetadata) => {
      if (!target) return;
      setSaveState("saving");
      try {
        const res = await upsertMetadata(processId, {
          owner_type: target.ownerType,
          owner_id: target.ownerId,
          metadata: payload,
        });
        setSaveState("saved");
        onSaved(target.ownerType, target.ownerId, res.metadata);
      } catch {
        setSaveState("error");
      }
    },
    [processId, target, onSaved]
  );

  const scheduleNodeSave = useCallback(
    (next: NodeTaskMetadata) => {
      latestNode.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void persist(latestNode.current);
      }, DEBOUNCE_MS);
    },
    [persist]
  );

  const updateNodeForm = (
    updater: (prev: NodeTaskMetadata) => NodeTaskMetadata,
    options?: { save?: boolean }
  ) => {
    setNodeForm((prev) => {
      const next = updater(prev);
      if (options?.save !== false) {
        scheduleNodeSave(next);
      } else {
        latestNode.current = next;
      }
      return next;
    });
  };

  const updateSourceRow = (
    index: number,
    key: keyof DataSourceProcedure,
    value: string | boolean
  ) => {
    updateNodeForm((prev) => ({
      ...prev,
      data_sources: prev.data_sources.map((row, i) =>
        i === index ? { ...row, [key]: value } : row
      ),
    }));
  };

  const addSourceRow = () => {
    updateNodeForm(
      (prev) => ({
        ...prev,
        data_sources: [
          ...prev.data_sources,
          {
            source_name: "",
            human_procedure: "",
            data_destinations: "",
            is_intermediate: false,
          },
        ],
      }),
      { save: false }
    );
  };

  const removeSourceRow = (index: number) => {
    updateNodeForm((prev) => ({
      ...prev,
      data_sources: prev.data_sources.filter((_, i) => i !== index),
    }));
  };

  const handleDelegateToAI = async () => {
    if (!target || target.ownerType !== "node") return;
    setDelegateBusy(true);
    setDelegateDialog(null);
    try {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      await persist(latestNode.current);
      const res = await fetch("/api/process/delegate-planning", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          process_instance_id: processId,
          target_node_id: target.ownerId,
          final_activity: latestNode.current.final_activity,
          finalized_artifact: latestNode.current.output_end_product,
          subtasks: latestNode.current.data_sources,
        }),
      });
      const started = (await res.json()) as DelegatePlanningResult;
      if (!res.ok || started.ok === false) {
        setDelegateDialog({
          variant: "error",
          title: "Delegation Failed",
          message: started.toast_message || `Delegation failed (${res.status})`,
          result: started,
        });
        return;
      }

      const traceId = started.trace_id;
      if (!traceId) {
        setDelegateDialog({
          variant: "error",
          title: "Delegation Failed",
          message: "Workflow started but no trace_id was returned.",
          result: started,
        });
        return;
      }

      setDelegateDialog({
        variant: "running",
        title: "Workflow Running",
        message: started.toast_message ?? "Waiting for crew_kickoff_completed…",
      });

      const deadline = Date.now() + DELEGATE_POLL_TIMEOUT_MS;
      let finalResult: DelegatePlanningResult = started;

      while (Date.now() < deadline) {
        await sleep(DELEGATE_POLL_INTERVAL_MS);
        const pollRes = await fetch(
          `/api/process/delegate-planning/poll?trace_id=${encodeURIComponent(traceId)}`,
          { credentials: "include" }
        );
        const polled = (await pollRes.json()) as DelegatePlanningResult;
        finalResult = polled;

        if (polled.status === "running" && polled.ok !== false) {
          continue;
        }

        if (polled.ok !== false && polled.status === "completed") {
          if (polled.metadata) {
            const next = normalizeNodeInitial(polled.metadata);
            setNodeForm(next);
            latestNode.current = next;
            onSaved(target.ownerType, target.ownerId, polled.metadata);
          }
          setDelegateDialog({
            variant: "success",
            title: "Delegation Complete",
            message: polled.toast_message ?? "Workflow completed successfully.",
            result: polled,
          });
          return;
        }

        setDelegateDialog({
          variant: "error",
          title: "Delegation Failed",
          message:
            polled.toast_message ||
            (pollRes.ok ? "Workflow did not complete successfully." : `Poll failed (${pollRes.status})`),
          result: polled,
        });
        return;
      }

      setDelegateDialog({
        variant: "error",
        title: "Delegation Timed Out",
        message:
          finalResult.toast_message ??
          `Workflow polling timed out after ${DELEGATE_POLL_TIMEOUT_MS / 1000}s.`,
        result: finalResult,
      });
    } catch (e) {
      setDelegateDialog({
        variant: "error",
        title: "Delegation Failed",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setDelegateBusy(false);
    }
  };

  if (!target) return null;

  const isGroupPanel = target.ownerType === "group";
  const statusLabel =
    saveState === "saving"
      ? "Saving…"
      : saveState === "saved"
      ? "Saved"
      : saveState === "error"
      ? "Save failed"
      : "";

  return (
    <>
      {delegateDialog && (
        <div
          className="delegate-overlay"
          role="presentation"
          onClick={() => setDelegateDialog(null)}
        >
          <div
            className={`delegate-dialog delegate-dialog--${delegateDialog.variant}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delegate-dialog-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="delegate-dialog__header">
              <h2 id="delegate-dialog-title">{delegateDialog.title}</h2>
              <button
                type="button"
                className="btn btn--sm delegate-dialog__close"
                onClick={() => setDelegateDialog(null)}
                aria-label="Close delegation result"
              >
                ×
              </button>
            </header>
            <div className="delegate-dialog__body">
              <p className="delegate-dialog__message">{delegateDialog.message}</p>
              {delegateDialog.result?.gateway_message && (
                <p className="delegate-dialog__detail">{delegateDialog.result.gateway_message}</p>
              )}
              {delegateDialog.variant === "success" && delegateDialog.result?.final_result != null && (
                <>
                  <h3 className="delegate-dialog__events-title">Workflow Result</h3>
                  <pre className="delegate-dialog__events delegate-dialog__events--primary">
                    {formatJsonBlock(delegateDialog.result.final_result)}
                  </pre>
                </>
              )}
            </div>
            <footer className="delegate-dialog__footer">
              {delegateDialog.variant === "running" ? (
                <span className="delegate-dialog__spinner" aria-live="polite">
                  Polling workflow…
                </span>
              ) : (
                <button
                  type="button"
                  className="btn btn--agentic"
                  onClick={() => setDelegateDialog(null)}
                >
                  Close
                </button>
              )}
            </footer>
          </div>
        </div>
      )}

      <aside
      className={`metadata-popover${isGroupPanel ? "" : " metadata-popover--wide"}`}
      style={isGroupPanel ? undefined : { width: TASK_EDITOR_WIDTH_PX }}
      role="dialog"
      aria-label={isGroupPanel ? "Assistant group consolidation" : "Task data mechanics"}
    >
      {isGroupPanel ? (
        <AssistantGroupPopover
          title={target.title}
          variant={target.variant ?? "default"}
          pipeline={target.aggregatedPipeline ?? EMPTY_AGGREGATED_PIPELINE}
          workflow={target.workflow}
          groupNodeIds={target.groupNodeIds}
          onClose={onClose}
          onRejectProposal={onRejectProposal}
        />
      ) : (
        <>
          <div className="metadata-popover__header">
            <h3>{target.title}</h3>
            <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>
          <p className="metadata-popover__hint">
            Break down inbound data sources, human verification routines, and outputs. Changes
            save automatically.
          </p>

          <section className="metadata-section">
            <h4 className="metadata-section__title">
              Data Sources &amp; Procedures (Inputs)
            </h4>
            {nodeForm.data_sources.length === 0 ? (
              <p className="metadata-section__empty">No data sources yet. Add one below.</p>
            ) : (
              <ul className="metadata-source-list">
                {nodeForm.data_sources.map((row, index) => (
                  <li key={index} className="metadata-source-row">
                    <div className="metadata-source-row__grid">
                      <div className="metadata-source-row__col-left">
                        <label className="metadata-field">
                          <span>Source Name</span>
                          <input
                            type="text"
                            value={row.source_name}
                            placeholder="e.g. legacy billing DB"
                            onChange={(e) =>
                              updateSourceRow(index, "source_name", e.target.value)
                            }
                          />
                        </label>
                        <label className="metadata-field">
                          <span>Data Destinations</span>
                          <input
                            type="text"
                            value={row.data_destinations}
                            placeholder="e.g., Claims Core Database"
                            onChange={(e) =>
                              updateSourceRow(index, "data_destinations", e.target.value)
                            }
                          />
                        </label>
                        <label className="metadata-field metadata-field--checkbox">
                          <input
                            type="checkbox"
                            checked={row.is_intermediate}
                            onChange={(e) =>
                              updateSourceRow(index, "is_intermediate", e.target.checked)
                            }
                          />
                          <span>Intermediate:</span>
                        </label>
                      </div>
                      <div className="metadata-source-row__col-right">
                        <label className="metadata-field metadata-field--stretch">
                          <span>Human Procedure</span>
                          <textarea
                            rows={6}
                            value={row.human_procedure}
                            placeholder="Describe the steps you perform on this data."
                            onChange={(e) =>
                              updateSourceRow(index, "human_procedure", e.target.value)
                            }
                          />
                        </label>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="btn btn--sm btn--row-remove"
                      onClick={() => removeSourceRow(index)}
                    >
                      Remove source
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <button type="button" className="btn btn--sm metadata-source-add" onClick={addSourceRow}>
              + Add data source
            </button>
          </section>

          <section className="metadata-section">
            <h4 className="metadata-section__title">Execution &amp; Outputs</h4>
            <label className="metadata-field">
              <span>Final Activity (Human Verification Routine):</span>
              <textarea
                rows={3}
                value={nodeForm.final_activity}
                placeholder="Describe the human verification steps performed before handoff."
                onChange={(e) =>
                  updateNodeForm((prev) => ({
                    ...prev,
                    final_activity: e.target.value,
                  }))
                }
              />
            </label>
            <label className="metadata-field">
              <span>Finalized Artifact</span>
              <input
                type="text"
                value={nodeForm.output_end_product}
                placeholder='e.g. "Finalized Fraud Evaluation Dossier"'
                onChange={(e) =>
                  updateNodeForm((prev) => ({
                    ...prev,
                    output_end_product: e.target.value,
                  }))
                }
              />
            </label>
            <button
              type="button"
              className="btn btn--agentic metadata-delegate-btn"
              disabled={delegateBusy}
              onClick={() => void handleDelegateToAI()}
            >
              {delegateBusy ? "Polling workflow…" : "Delegate to AI"}
            </button>
          </section>

          <p className={`metadata-popover__status metadata-popover__status--${saveState}`}>
            {statusLabel}
          </p>
        </>
      )}
    </aside>
    </>
  );
}
