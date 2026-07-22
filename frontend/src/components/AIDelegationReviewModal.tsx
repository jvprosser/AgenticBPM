import type { AIDelegationPayload, AugmentedDataSource } from "../lib/delegationFormatting";
import { formatStructuredText } from "../lib/delegationFormatting";
import CatalogMetadataCard from "./CatalogMetadataCard";

interface Props {
  open: boolean;
  payload: AIDelegationPayload;
  onCancel: () => void;
  onAccept: () => void;
}

function ReadOnlyField({
  label,
  value,
  multiline = false,
}: {
  label: string;
  value: string;
  multiline?: boolean;
}) {
  return (
    <label className="metadata-field metadata-field--readonly">
      <span>{label}</span>
      {multiline ? (
        <textarea readOnly rows={6} value={value} className="metadata-field__readonly" />
      ) : (
        <input readOnly type="text" value={value} className="metadata-field__readonly" />
      )}
    </label>
  );
}

export default function AIDelegationReviewModal({
  open,
  payload,
  onCancel,
  onAccept,
}: Props) {
  if (!open) return null;

  const sources = payload.data_sources ?? payload.subtasks ?? [];
  const outputProduct = payload.finalized_artifact ?? payload.output_end_product ?? "";

  return (
    <div className="delegate-overlay ai-review-overlay" role="presentation" onClick={onCancel}>
      <div
        className="delegate-dialog delegate-dialog--success ai-delegation-review"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-delegation-review-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="delegate-dialog__header">
          <h2 id="ai-delegation-review-title">AI Delegation Review</h2>
          <button
            type="button"
            className="btn btn--sm delegate-dialog__close"
            onClick={onCancel}
            aria-label="Close review"
          >
            ×
          </button>
        </header>

        <div className="delegate-dialog__body ai-delegation-review__body">
          <p className="delegate-dialog__message">
            Review the augmented task breakdown below. Accept to replace your current authoring
            template, or cancel to keep existing values.
          </p>

          <ReadOnlyField
            label="Primary Input Parameter"
            value={payload.input_parameter ?? ""}
          />
          <ReadOnlyField label="Finalized Artifact" value={outputProduct} />
          <label className="metadata-field metadata-field--checkbox metadata-field--readonly">
            <input
              readOnly
              type="checkbox"
              checked={Boolean(payload.user_validation_required)}
              tabIndex={-1}
            />
            <span>User Validation Required</span>
          </label>
          <ReadOnlyField
            label="Final Activity (User Verification Routine)"
            value={formatStructuredText(payload.final_activity)}
            multiline
          />

          <section className="ai-delegation-review__sources">
            <h3 className="delegate-dialog__events-title">Inbound Data Sources</h3>
            {sources.length === 0 ? (
              <p className="metadata-section__empty">No data sources were returned.</p>
            ) : (
              <ul className="ai-delegation-review__source-list">
                {sources.map((source: AugmentedDataSource, index: number) => (
                  <li key={`${source.source_name ?? "source"}-${index}`} className="ai-delegation-review__source">
                    <ReadOnlyField label="Source Name" value={source.source_name ?? ""} />
                    <ReadOnlyField
                      label="Data Destinations"
                      value={source.data_destinations ?? ""}
                    />
                    <ReadOnlyField
                      label="User Procedure"
                      value={formatStructuredText(
                        source.user_procedure ?? source.human_procedure
                      )}
                      multiline
                    />
                    <CatalogMetadataCard source={source} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <footer className="delegate-dialog__footer ai-delegation-review__footer">
          <button type="button" className="btn btn--sm" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn--agentic btn--sm" onClick={onAccept}>
            Accept
          </button>
        </footer>
      </div>
    </div>
  );
}
