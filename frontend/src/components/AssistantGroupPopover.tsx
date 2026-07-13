import { useState } from "react";
import type { AggregatedPipeline, SuggestWorkflow } from "../api";

interface Props {
  title: string;
  variant: "default" | "proposed";
  pipeline: AggregatedPipeline;
  workflow?: SuggestWorkflow | null;
  groupNodeIds?: string[];
  onClose: () => void;
  onRejectProposal?: (nodeIds: string[]) => Promise<void>;
}

export default function AssistantGroupPopover({
  title,
  variant,
  pipeline,
  workflow,
  groupNodeIds,
  onClose,
  onRejectProposal,
}: Props) {
  const [rejectBusy, setRejectBusy] = useState(false);
  const [rejectError, setRejectError] = useState<string | null>(null);
  const isProposed = variant === "proposed";

  const handleReject = async () => {
    if (!groupNodeIds?.length || !onRejectProposal) return;
    setRejectBusy(true);
    setRejectError(null);
    try {
      await onRejectProposal(groupNodeIds);
      onClose();
    } catch (e) {
      setRejectError(e instanceof Error ? e.message : String(e));
    } finally {
      setRejectBusy(false);
    }
  };

  return (
    <>
      <div className="metadata-popover__header">
        <h3>{title}</h3>
        <button type="button" className="btn btn--sm" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <p className="metadata-popover__hint">
        {isProposed
          ? "Functional consolidation cockpit — AI Assistant Proposal."
          : "Functional consolidation cockpit — aggregated from enclosed tasks."}
      </p>

      <section className="metadata-section">
        <h4 className="metadata-section__title">Target Workflow Scope</h4>
        {pipeline.scope_tasks.length === 0 ? (
          <p className="metadata-section__empty">No tasks are assigned to this group yet.</p>
        ) : (
          <ul className="pipeline-scope-list">
            {pipeline.scope_tasks.map((task) => (
              <li key={task.id}>{task.label}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="metadata-section">
        <h4 className="metadata-section__title">Aggregated Data Pipeline</h4>
        {pipeline.data_sources.length === 0 ? (
          <p className="metadata-section__empty">
            No inbound data sources documented on enclosed tasks yet.
          </p>
        ) : (
          <ul className="pipeline-source-list">
            {pipeline.data_sources.map((source) => (
              <li key={source.source_name} className="pipeline-accordion">
                <details>
                  <summary className="pipeline-accordion__summary">{source.source_name}</summary>
                  <div className="pipeline-accordion__body">
                    {source.human_procedures.length === 0 ? (
                      <p className="metadata-section__empty">No corresponding process documented.</p>
                    ) : (
                      <ul className="pipeline-procedure-list">
                        {source.human_procedures.map((procedure, index) => (
                          <li key={`${source.source_name}:${index}`}>{procedure}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        )}
        {pipeline.output_products.length > 0 && (
          <div className="pipeline-outputs">
            <span className="pipeline-outputs__label">Target output products</span>
            <ul className="pipeline-scope-list">
              {pipeline.output_products.map((product) => (
                <li key={product}>{product}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="metadata-section">
        <h4 className="metadata-section__title">Assistant Task Blueprint</h4>
        {!workflow ? (
          <p className="metadata-section__empty">
            No AI execution plan generated for this group yet.
          </p>
        ) : (
          <div className="workflow-blueprint">
            {isProposed && (
              <p className="workflow-blueprint__lead">
                Proposed digital assistant translation for the aggregated manual procedures
                above, delivering the documented output products.
              </p>
            )}
            <div className="workflow-blueprint__block">
              <span className="workflow-blueprint__label">Workflow</span>
              <p className="metadata-rationale">{workflow.workflow_name}</p>
            </div>
            {workflow.agents.map((agent) => (
              <div key={agent.name} className="workflow-blueprint__block">
                <span className="workflow-blueprint__label">{agent.name}</span>
                <p className="metadata-rationale">{agent.goal}</p>
                {agent.tools.length > 0 && (
                  <p className="workflow-blueprint__meta">Tools: {agent.tools.join(", ")}</p>
                )}
              </div>
            ))}
            {workflow.tasks.map((task, index) => (
              <div key={`${task.agent}:${index}`} className="workflow-blueprint__block">
                <span className="workflow-blueprint__label">Task · {task.agent}</span>
                <p className="metadata-rationale">{task.description}</p>
              </div>
            ))}
            {workflow.rationale && (
              <div className="workflow-blueprint__block">
                <span className="workflow-blueprint__label">Rationale</span>
                <p className="metadata-rationale">{workflow.rationale}</p>
              </div>
            )}
          </div>
        )}
      </section>

      {isProposed && (
        <>
          <button
            type="button"
            className="btn btn--reject"
            disabled={rejectBusy || !groupNodeIds?.length}
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
      )}
    </>
  );
}
