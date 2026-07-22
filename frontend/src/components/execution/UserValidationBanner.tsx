import { useState } from "react";
import type { SubtaskExecutionRecord } from "../../api";

interface Props {
  execution: SubtaskExecutionRecord;
  busy: boolean;
  onApprove: (executionId: string, feedback: string) => Promise<void>;
  onReject: (executionId: string, feedback: string) => Promise<void>;
}

function formatPayload(payload: Record<string, unknown> | null | undefined): string {
  if (!payload || Object.keys(payload).length === 0) {
    return "No generated result summary is available yet.";
  }
  return JSON.stringify(payload, null, 2);
}

export default function UserValidationBanner({ execution, busy, onApprove, onReject }: Props) {
  const [feedback, setFeedback] = useState("");

  return (
    <div className="user-validation-banner">
      <h5 className="user-validation-banner__title">🛡️ User Validation Required</h5>
      <pre className="user-validation-banner__summary">
        {formatPayload(execution.output_payload ?? null)}
      </pre>
      <label className="metadata-field">
        <span>Validation Notes</span>
        <textarea
          rows={3}
          value={feedback}
          placeholder="Enter validation notes or override comments..."
          onChange={(e) => setFeedback(e.target.value)}
        />
      </label>
      <div className="user-validation-banner__actions">
        <button
          type="button"
          className="btn btn--agentic btn--sm"
          disabled={busy}
          onClick={() => void onApprove(execution.id, feedback)}
        >
          Approve &amp; Proceed
        </button>
        <button
          type="button"
          className="btn btn--sm btn--danger"
          disabled={busy}
          onClick={() => void onReject(execution.id, feedback)}
        >
          Reject &amp; Re-run
        </button>
      </div>
    </div>
  );
}
