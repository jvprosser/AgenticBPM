import { useCallback, useEffect, useRef, useState } from "react";
import {
  upsertMetadata,
  EMPTY_GROUP_METADATA,
  EMPTY_NODE_TASK_METADATA,
  isNodeTaskMetadata,
  type DataSourceProcedure,
  type GroupMetadataRecord,
  type NodeTaskMetadata,
  type SuggestWorkflow,
} from "../api";

const DEBOUNCE_MS = 400;

export interface MetadataTarget {
  ownerType: "node" | "group";
  ownerId: string;
  title: string;
  variant?: "default" | "proposed";
  rationale?: string;
  groupNodeIds?: string[];
  workflow?: SuggestWorkflow | null;
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

function normalizeGroupInitial(initial: NodeTaskMetadata | GroupMetadataRecord): GroupMetadataRecord {
  if (isNodeTaskMetadata(initial)) {
    return { ...EMPTY_GROUP_METADATA };
  }
  return {
    name: initial.name ?? null,
    owner: initial.owner ?? null,
    description: initial.description ?? null,
  };
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
  const [groupForm, setGroupForm] = useState<GroupMetadataRecord>(normalizeGroupInitial(initial));
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [rejectBusy, setRejectBusy] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestNode = useRef(nodeForm);
  const latestGroup = useRef(groupForm);

  useEffect(() => {
    const nextNode = normalizeNodeInitial(initial);
    const nextGroup = normalizeGroupInitial(initial);
    setNodeForm(nextNode);
    setGroupForm(nextGroup);
    latestNode.current = nextNode;
    latestGroup.current = nextGroup;
    setSaveState("idle");
    setRejectError(null);
    // Reload from server only when the selected owner changes — not after our own saves.
  }, [target?.ownerId, target?.ownerType]);

  const persist = useCallback(
    async (payload: NodeTaskMetadata | GroupMetadataRecord) => {
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

  const scheduleGroupSave = useCallback(
    (next: GroupMetadataRecord) => {
      latestGroup.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void persist(latestGroup.current);
      }, DEBOUNCE_MS);
    },
    [persist]
  );

  const updateGroup = <K extends keyof GroupMetadataRecord>(
    key: K,
    value: GroupMetadataRecord[K]
  ) => {
    setGroupForm((prev) => {
      const next = { ...prev, [key]: value };
      scheduleGroupSave(next);
      return next;
    });
  };

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

  const handleReject = async () => {
    if (!target?.groupNodeIds?.length || !onRejectProposal) return;
    setRejectBusy(true);
    setRejectError(null);
    try {
      await onRejectProposal(target.groupNodeIds);
      onClose();
    } catch (e) {
      setRejectError(e instanceof Error ? e.message : String(e));
    } finally {
      setRejectBusy(false);
    }
  };

  if (!target) return null;

  const isProposed = target.variant === "proposed";
  const isGroupCharter = target.ownerType === "group";
  const workflow = target.workflow;
  const leadAgent = workflow?.agents[0];

  const statusLabel =
    isProposed
      ? ""
      : saveState === "saving"
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
      aria-label={isGroupCharter ? "Project charter" : "Task data mechanics"}
    >
      <div className="metadata-popover__header">
        <h3>{isGroupCharter ? "Project Charter" : target.title}</h3>
        <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {isGroupCharter && isProposed ? (
        <>
          <p className="metadata-popover__hint">Governance review — AI Assistant Proposal.</p>
          <div className="metadata-field">
            <span>Assistant Workflow Name</span>
            <p className="metadata-rationale">
              {workflow?.workflow_name ?? groupForm.name ?? "—"}
            </p>
          </div>
          <div className="metadata-field">
            <span>Assigned Operational Goal</span>
            <p className="metadata-rationale">
              {leadAgent?.goal ?? workflow?.tasks[0]?.description ?? groupForm.description ?? "—"}
            </p>
          </div>
          <div className="metadata-field">
            <span>Assistant Task Mapping</span>
            <p className="metadata-rationale">{leadAgent?.backstory ?? groupForm.owner ?? "—"}</p>
          </div>
          <div className="metadata-field">
            <span>Assigned Platform Tools</span>
            <p className="metadata-rationale">
              {leadAgent?.tools?.length ? leadAgent.tools.join(", ") : "—"}
            </p>
          </div>
          {target.rationale && (
            <div className="metadata-field">
              <span>Optimization Rationale</span>
              <p className="metadata-rationale">{target.rationale}</p>
            </div>
          )}
          <button
            type="button"
            className="btn btn--reject"
            disabled={rejectBusy || !target.groupNodeIds?.length}
            onClick={() => void handleReject()}
          >
            {rejectBusy ? "Rejecting…" : "Reject Proposal"}
          </button>
          {rejectError && (
            <p className="metadata-popover__status metadata-popover__status--error">{rejectError}</p>
          )}
          <button type="button" className="btn btn--agentic" disabled title="Available in Step 5d">
            Assistant Options
          </button>
        </>
      ) : isGroupCharter ? (
        <>
          <p className="metadata-popover__hint">Executive governance charter — changes save automatically.</p>
          <label className="metadata-field">
            <span>Assistant Workflow Name</span>
            <input
              type="text"
              value={groupForm.name ?? ""}
              onChange={(e) => updateGroup("name", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Assigned Operational Goal</span>
            <textarea
              rows={3}
              value={groupForm.description ?? ""}
              onChange={(e) => updateGroup("description", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Assistant Task Mapping</span>
            <input
              type="text"
              value={groupForm.owner ?? ""}
              onChange={(e) => updateGroup("owner", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Assigned Platform Tools</span>
            <input
              type="text"
              value={workflow ? (leadAgent?.tools ?? []).join(", ") : ""}
              readOnly
              placeholder="Populated when linked to Agent Studio"
            />
          </label>
          <p className={`metadata-popover__status metadata-popover__status--${saveState}`}>
            {statusLabel}
          </p>
        </>
      ) : (
        <>
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
                      <span>Historical human procedure</span>
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
