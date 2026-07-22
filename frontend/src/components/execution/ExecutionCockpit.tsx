import { useCallback, useEffect, useState } from "react";
import {
  approveSubtaskExecution,
  fetchExecutionArtifact,
  getClaimDetail,
  listProcessClaims,
  rejectSubtaskExecution,
  runClaim,
  type ClaimDetailResponse,
  type ClaimInstanceRecord,
  type DataSourceProcedure,
  type SubtaskExecutionRecord,
} from "../../api";
import ArtifactInspectorModal from "./ArtifactInspectorModal";
import ClaimLauncherDrawer from "./ClaimLauncherDrawer";
import ExecutionPipelineView from "./ExecutionPipelineView";

interface Props {
  processId: string;
  nodeId: string;
  inputParameter: string;
  subtasks: DataSourceProcedure[];
}

function claimStatusClass(status: string): string {
  switch (status) {
    case "PROCESSING":
      return "exec-badge exec-badge--running";
    case "AWAITING_USER_VALIDATION":
      return "exec-badge exec-badge--validation";
    case "COMPLETED":
      return "exec-badge exec-badge--success";
    case "FAILED":
      return "exec-badge exec-badge--failed";
    default:
      return "exec-badge exec-badge--pending";
  }
}

function formatArtifactContent(value: unknown): string {
  if (value === undefined || value === null) return "No artifact content.";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export default function ExecutionCockpit({
  processId,
  nodeId,
  inputParameter,
  subtasks,
}: Props) {
  const [claims, setClaims] = useState<ClaimInstanceRecord[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string>("");
  const [claimDetail, setClaimDetail] = useState<ClaimDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [artifactOpen, setArtifactOpen] = useState(false);
  const [artifactTitle, setArtifactTitle] = useState("Artifact");
  const [artifactContent, setArtifactContent] = useState("");
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshClaims = useCallback(async (preferredClaimId?: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await listProcessClaims(processId, nodeId);
      setClaims(rows);
      if (rows.length === 0) {
        setSelectedClaimId("");
        setClaimDetail(null);
        return;
      }
      const activeId =
        preferredClaimId && rows.some((row) => row.id === preferredClaimId)
          ? preferredClaimId
          : rows[0].id;
      setSelectedClaimId(activeId);
      const detail = await getClaimDetail(activeId);
      setClaimDetail(detail);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [processId, nodeId]);

  useEffect(() => {
    void refreshClaims();
  }, [refreshClaims]);

  const loadClaimDetail = async (claimId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const detail = await getClaimDetail(claimId);
      setClaimDetail(detail);
      setSelectedClaimId(claimId);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleRunClaim = async (claimNumber: string, parameters: Record<string, string>) => {
    const detail = await runClaim({
      process_id: processId,
      target_node_id: nodeId,
      claim_number: claimNumber,
      claim_parameters: parameters,
    });
    setClaims((prev) => {
      const next = [detail.claim, ...prev.filter((item) => item.id !== detail.claim.id)];
      return next;
    });
    setSelectedClaimId(detail.claim.id);
    setClaimDetail(detail);
  };

  const handleViewArtifact = async (execution: SubtaskExecutionRecord) => {
    setArtifactOpen(true);
    setArtifactTitle(execution.subtask_name || execution.subtask_id);
    setArtifactLoading(true);
    setArtifactError(null);
    setArtifactContent("");
    try {
      const payload = await fetchExecutionArtifact(execution.id);
      setArtifactContent(formatArtifactContent(payload));
    } catch (e) {
      if (execution.output_payload) {
        setArtifactContent(formatArtifactContent(execution.output_payload));
      } else {
        setArtifactError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setArtifactLoading(false);
    }
  };

  const applyDetail = (detail: ClaimDetailResponse) => {
    setClaimDetail(detail);
    setClaims((prev) =>
      prev.map((item) => (item.id === detail.claim.id ? detail.claim : item))
    );
  };

  const handleApprove = async (executionId: string, feedback: string) => {
    setActionBusy(true);
    try {
      const detail = await approveSubtaskExecution(executionId, feedback);
      applyDetail(detail);
    } finally {
      setActionBusy(false);
    }
  };

  const handleReject = async (executionId: string, feedback: string) => {
    setActionBusy(true);
    try {
      const detail = await rejectSubtaskExecution(executionId, feedback);
      applyDetail(detail);
    } finally {
      setActionBusy(false);
    }
  };

  const activeClaim = claimDetail?.claim ?? claims.find((item) => item.id === selectedClaimId);

  return (
    <>
      <section className="metadata-section exec-cockpit">
        <div className="exec-cockpit__bar">
          <label className="exec-cockpit__selector metadata-field">
            <span>Claim Run</span>
            <select
              value={selectedClaimId}
              disabled={loading || claims.length === 0}
              onChange={(e) => void loadClaimDetail(e.target.value)}
            >
              {claims.length === 0 ? <option value="">No claim runs yet</option> : null}
              {claims.map((claim) => (
                <option key={claim.id} value={claim.id}>
                  {claim.claim_number}
                </option>
              ))}
            </select>
          </label>
          {activeClaim ? (
            <span className={claimStatusClass(activeClaim.status)}>{activeClaim.status}</span>
          ) : null}
          <button
            type="button"
            className="btn btn--sm btn--agentic exec-cockpit__run-btn"
            onClick={() => setDrawerOpen(true)}
          >
            + Run New Claim
          </button>
        </div>
        {loadError ? <p className="exec-cockpit__error">{loadError}</p> : null}
        {loading && !claimDetail ? (
          <p className="metadata-section__empty">Loading execution pipeline…</p>
        ) : (
          <ExecutionPipelineView
            subtasks={subtasks}
            executions={claimDetail?.subtask_executions ?? []}
            actionBusy={actionBusy}
            onViewArtifact={(execution) => void handleViewArtifact(execution)}
            onApprove={handleApprove}
            onReject={handleReject}
          />
        )}
      </section>

      <ClaimLauncherDrawer
        open={drawerOpen}
        inputParameter={inputParameter}
        onClose={() => setDrawerOpen(false)}
        onSubmit={handleRunClaim}
      />

      <ArtifactInspectorModal
        open={artifactOpen}
        title={artifactTitle}
        content={artifactContent}
        loading={artifactLoading}
        error={artifactError}
        onClose={() => setArtifactOpen(false)}
      />
    </>
  );
}
