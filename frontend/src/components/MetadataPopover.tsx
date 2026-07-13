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

function normalizeNodeInitial(initial: NodeTaskMetadata | GroupMetadataRecord): NodeTaskMetadata {
  if (isNodeTaskMetadata(initial)) {
    return {
      data_sources: initial.data_sources.map((row) => ({
        source_name: row.source_name ?? "",
        human_procedure: row.human_procedure ?? "",
      })),
      output_end_product: initial.output_end_product ?? "",
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
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestNode = useRef(nodeForm);

  useEffect(() => {
    const nextNode = normalizeNodeInitial(initial);
    setNodeForm(nextNode);
    latestNode.current = nextNode;
    setSaveState("idle");
  }, [target?.ownerId, target?.ownerType]);

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
    value: string
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
        data_sources: [...prev.data_sources, { source_name: "", human_procedure: "" }],
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
    <aside
      className="metadata-popover"
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
            List required data sources with associated actions and the output from this task.
            Changes save automatically.
          </p>

          <section className="metadata-section">
            <h4 className="metadata-section__title">
              Inbound Data Sources &amp; Historical Procedures (Inputs)
            </h4>
            {nodeForm.data_sources.length === 0 ? (
              <p className="metadata-section__empty">No data sources yet. Add one below.</p>
            ) : (
              <ul className="metadata-source-list">
                {nodeForm.data_sources.map((row, index) => (
                  <li key={index} className="metadata-source-row">
                    <label className="metadata-field">
                      <span>Data source</span>
                      <input
                        type="text"
                        value={row.source_name}
                        placeholder='e.g. "Fraud Engine Telemetry"'
                        onChange={(e) => updateSourceRow(index, "source_name", e.target.value)}
                      />
                    </label>
                    <label className="metadata-field">
                      <span>Corresponding process</span>
                      <textarea
                        rows={3}
                        value={row.human_procedure}
                        placeholder="Describe the manual steps people perform on this data source."
                        onChange={(e) => updateSourceRow(index, "human_procedure", e.target.value)}
                      />
                    </label>
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
            <h4 className="metadata-section__title">Output End Product (Outputs)</h4>
            <label className="metadata-field">
              <span>Finalized artifact or routing asset</span>
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
          </section>

          <p className={`metadata-popover__status metadata-popover__status--${saveState}`}>
            {statusLabel}
          </p>
        </>
      )}
    </aside>
  );
}
