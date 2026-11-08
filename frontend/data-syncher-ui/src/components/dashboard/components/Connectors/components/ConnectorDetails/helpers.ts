import { type Connector, type SchemaStatusResponse } from "@/types/connectors";

type StatusParams = {
  isUpdateSchemaInProgress: number;
  isRefreshSchemaInProgress: number;
  isAnyOperationInProgress: boolean;
  schemaStatus?: SchemaStatusResponse | null;
};

export const normalizeConnectorName = (name?: string | null) =>
  name?.toLowerCase().replace(/[\s\-._]/g, "") || "";

export const isSnowflakeToSnowflakeConnector = (
  connector?: Pick<Connector, "source_name" | "destination_name">,
) =>
  normalizeConnectorName(connector?.source_name) === "snowflake" &&
  normalizeConnectorName(connector?.destination_name) === "snowflake";

export const getStatusMessage = ({
  isUpdateSchemaInProgress,
  isRefreshSchemaInProgress,
  isAnyOperationInProgress,
  schemaStatus,
}: StatusParams) => {
  // Show progress if schema update is in progress with table counts
  if (schemaStatus?.is_in_progress) {
    if (
      schemaStatus.tables_fetched !== undefined &&
      schemaStatus.total_tables !== undefined
    ) {
      return `Fetching tables (${schemaStatus.tables_fetched}/${schemaStatus.total_tables})...`;
    }
    return "Updating schema...";
  }
  if (isUpdateSchemaInProgress > 0) {
    return "Updating schema...";
  }
  if (isRefreshSchemaInProgress > 0) {
    return "Refreshing schema...";
  }
  if (isAnyOperationInProgress) {
    return "Sync in progress";
  }
  return null;
};
