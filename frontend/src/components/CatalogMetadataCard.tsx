import type { DataSourceProcedure } from "../api";
import type { AugmentedDataSource } from "../lib/delegationFormatting";
import { formatCatalogValue, hasCatalogMetadata } from "../lib/delegationFormatting";

interface Props {
  source: Partial<DataSourceProcedure> | AugmentedDataSource;
}

export default function CatalogMetadataCard({ source }: Props) {
  if (!hasCatalogMetadata(source)) return null;

  const rows: Array<{ label: string; value: string }> = [];
  if (source.qualified_name) {
    rows.push({ label: "Qualified Name", value: String(source.qualified_name) });
  }
  if (source.business_terms) {
    rows.push({ label: "Business Terms", value: formatCatalogValue(source.business_terms) });
  }
  if (source.classifications) {
    rows.push({ label: "Classifications", value: formatCatalogValue(source.classifications) });
  }
  if (source.asset_type) {
    rows.push({ label: "Asset Type", value: String(source.asset_type) });
  }
  if (source.owner) {
    rows.push({ label: "Owner", value: String(source.owner) });
  }
  if (source.description) {
    rows.push({ label: "Description", value: String(source.description) });
  }
  if (source.destination) {
    rows.push({ label: "Platform Destination", value: String(source.destination) });
  }

  return (
    <div className="catalog-metadata-card">
      <h5 className="catalog-metadata-card__title">Catalog Metadata</h5>
      <dl className="catalog-metadata-card__list">
        {rows.map((row) => (
          <div key={row.label} className="catalog-metadata-card__row">
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
