import { type BodyFieldOption } from "./types";

export const BODY_FIELDS_OPTIONS: BodyFieldOption[] = [
  { id: "service", label: "Service name" },
  { id: "connection", label: "Connection name" },
  { id: "company", label: "Company name" },
  { id: "table", label: "Table name" },
  { id: "file", label: "File" },
  { id: "rows", label: "Rows Exported" },
  { id: "columns", label: "Columns Exported" },
  { id: "path", label: "Destination Path" },
  { id: "timestamp", label: "Completed timestamp" },
];

export const DEFAULT_BODY_FIELDS: string[] = [];

export const DEFAULT_SUBJECT_TEMPLATE =
  "The {destination} export for {table} finished successfully.";
export const DEFAULT_BODY_CONTENT = "";
export const DEFAULT_TEAM_NAME = "Thanks & Regards,";
