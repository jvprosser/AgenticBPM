import { useCallback, useEffect, useRef, useState } from "react";
import {
  upsertMetadata,
  type MetadataRecord,
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
  initial: MetadataRecord;
  onClose: () => void;
  onSaved: (ownerType: "node" | "group", ownerId: string, meta: MetadataRecord) => void;
  onRejectProposal?: (nodeIds: string[]) => Promise<void>;
}

type SaveState = "idle" | "saving" | "saved" | "error";

export default function MetadataPopover({
  processId,
  target,
  initial,
  onClose,
  onSaved,
  onRejectProposal,
}: Props) {
  const [form, setForm] = useState<MetadataRecord>(initial);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [rejectBusy, setRejectBusy] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef(form);

  useEffect(() => {
    setForm(initial);
    latest.current = initial;
    setSaveState("idle");
    setRejectError(null);
  }, [target?.ownerId, target?.ownerType, initial]);

  const persist = useCallback(
    async (payload: MetadataRecord) => {
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

  const scheduleSave = useCallback(
    (next: MetadataRecord) => {
      latest.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void persist(latest.current);
      }, DEBOUNCE_MS);
    },
    [persist]
  );

  const update = <K extends keyof MetadataRecord>(key: K, value: MetadataRecord[K]) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      scheduleSave(next);
      return next;
    });
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
    isProposed || isGroupCharter
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
      aria-label={isGroupCharter ? "Project charter" : "Metadata editor"}
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
              {workflow?.workflow_name ?? form.name ?? "—"}
            </p>
          </div>
          <div className="metadata-field">
            <span>Assigned Operational Goal</span>
            <p className="metadata-rationale">
              {leadAgent?.goal ?? workflow?.tasks[0]?.description ?? form.description ?? "—"}
            </p>
          </div>
          <div className="metadata-field">
            <span>Assistant Task Mapping</span>
            <p className="metadata-rationale">{leadAgent?.backstory ?? form.owner ?? "—"}</p>
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
          {rejectError && <p className="metadata-popover__status metadata-popover__status--error">{rejectError}</p>}
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
              value={form.name ?? ""}
              onChange={(e) => update("name", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Assigned Operational Goal</span>
            <textarea
              rows={3}
              value={form.description ?? ""}
              onChange={(e) => update("description", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Assistant Task Mapping</span>
            <input
              type="text"
              value={form.owner ?? ""}
              onChange={(e) => update("owner", e.target.value || null)}
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
            Changes save automatically ({target.ownerType}).
          </p>
          <label className="metadata-field">
            <span>Name</span>
            <input
              type="text"
              value={form.name ?? ""}
              onChange={(e) => update("name", e.target.value || null)}
            />
          </label>
          <label className="metadata-field">
            <span>Owner</span>
            <input
              type="text"
              value={form.owner ?? ""}
              onChange={(e) => update("owner", e.target.value || null)}
            />
          </label>
          <div className="metadata-field metadata-field--row">
            <label>
              <span>Expected duration</span>
              <input
                type="number"
                min={0}
                value={form.duration_value ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  update("duration_value", v === "" ? null : parseInt(v, 10));
                }}
              />
            </label>
            <label>
              <span>Unit</span>
              <select
                value={form.duration_unit ?? ""}
                onChange={(e) =>
                  update(
                    "duration_unit",
                    (e.target.value || null) as MetadataRecord["duration_unit"]
                  )
                }
              >
                <option value="">—</option>
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </label>
          </div>
          <label className="metadata-field">
            <span>Description</span>
            <textarea
              rows={4}
              value={form.description ?? ""}
              onChange={(e) => update("description", e.target.value || null)}
            />
          </label>
          <p className={`metadata-popover__status metadata-popover__status--${saveState}`}>
            {statusLabel}
          </p>
        </>
      )}
    </aside>
  );
}
