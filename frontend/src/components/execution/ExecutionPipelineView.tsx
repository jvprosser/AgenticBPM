import type { DataSourceProcedure, SubtaskExecutionRecord } from "../../api";
import type { ExecutionMode } from "./types";
import UserValidationBanner from "./UserValidationBanner";

interface Props {
  subtasks: DataSourceProcedure[];
  executions: SubtaskExecutionRecord[];
  actionBusy: boolean;
  onViewArtifact: (execution: SubtaskExecutionRecord) => void;
  onApprove: (executionId: string, feedback: string) => Promise<void>;
  onReject: (executionId: string, feedback: string) => Promise<void>;
}

function executionModeLabel(mode: string | undefined): string {
  return mode === "user_manual" ? "User Manual" : "Agent Automated";
}

function statusClass(status: string): string {
  switch (status) {
    case "RUNNING":
      return "exec-badge exec-badge--running";
    case "AWAITING_USER_VALIDATION":
      return "exec-badge exec-badge--validation";
    case "APPROVED":
    case "COMPLETED":
      return "exec-badge exec-badge--success";
    case "FAILED":
      return "exec-badge exec-badge--failed";
    default:
      return "exec-badge exec-badge--pending";
  }
}

function resolveExecutionMode(subtask: DataSourceProcedure | undefined): ExecutionMode {
  if (subtask?.execution_mode === "user_manual" || subtask?.execution_mode === "agent_automated") {
    return subtask.execution_mode;
  }
  return "agent_automated";
}

function matchSubtask(
  subtasks: DataSourceProcedure[],
  nodeId: string,
  execution: SubtaskExecutionRecord,
  index: number
): DataSourceProcedure | undefined {
  const byId = subtasks.find((item, itemIndex) => {
    const key = item.subtask_id?.trim() || `${nodeId}:subtask:${itemIndex}`;
    return key === execution.subtask_id;
  });
  if (byId) return byId;
  return subtasks[index];
}

interface PipelineItemProps {
  execution: SubtaskExecutionRecord;
  subtask?: DataSourceProcedure;
  actionBusy: boolean;
  onViewArtifact: (execution: SubtaskExecutionRecord) => void;
  onApprove: (executionId: string, feedback: string) => Promise<void>;
  onReject: (executionId: string, feedback: string) => Promise<void>;
}

function PipelineItem({
  execution,
  subtask,
  actionBusy,
  onViewArtifact,
  onApprove,
  onReject,
}: PipelineItemProps) {
  const mode = resolveExecutionMode(subtask);
  const hasArtifact = Boolean(execution.artifact_path || execution.output_payload);

  return (
    <li className="exec-pipeline-item">
      <div className="exec-pipeline-item__header">
        <div>
          <h5 className="exec-pipeline-item__title">
            {execution.subtask_name || execution.subtask_id}
          </h5>
          <p className="exec-pipeline-item__mode">{executionModeLabel(mode)}</p>
        </div>
        <span className={statusClass(execution.status)}>{execution.status}</span>
      </div>
      {execution.session_id ? (
        <p className="exec-pipeline-item__telemetry">Session ID: {execution.session_id}</p>
      ) : null}
      {execution.trace_id ? (
        <p className="exec-pipeline-item__telemetry">Trace ID: {execution.trace_id}</p>
      ) : null}
      {hasArtifact ? (
        <button
          type="button"
          className="btn btn--sm exec-pipeline-item__artifact-btn"
          onClick={() => onViewArtifact(execution)}
        >
          View Artifact
        </button>
      ) : null}
      {execution.status === "AWAITING_USER_VALIDATION" ? (
        <UserValidationBanner
          execution={execution}
          busy={actionBusy}
          onApprove={onApprove}
          onReject={onReject}
        />
      ) : null}
    </li>
  );
}

export default function ExecutionPipelineView({
  subtasks,
  executions,
  actionBusy,
  onViewArtifact,
  onApprove,
  onReject,
}: Props) {
  const nodePrefix = executions[0]?.subtask_id.split(":subtask:")[0] ?? "node";

  if (executions.length === 0) {
    return <p className="metadata-section__empty">No subtask executions for this claim run yet.</p>;
  }

  return (
    <ol className="exec-pipeline">
      {executions.map((execution, index) => (
        <PipelineItem
          key={execution.id}
          execution={execution}
          subtask={matchSubtask(subtasks, nodePrefix, execution, index)}
          actionBusy={actionBusy}
          onViewArtifact={onViewArtifact}
          onApprove={onApprove}
          onReject={onReject}
        />
      ))}
    </ol>
  );
}
