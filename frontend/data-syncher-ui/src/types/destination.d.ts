import { type FieldConfig } from "./form";

type MasterDestination = {
  dst_id: number;
  name: string;
};

export type MasterDestinationList = {
  content: Array<MasterDestination>;
  totalElements: number;
  size: number;
};

export interface NewDestinationFormState {
  dst: string;
  destinationName: string;
}

export type DatabricksSparkJobProvisionOutcome = {
  databricks_spark_job_provision_error?: string | null;
  databricks_spark_job_provision_status?: string | null;
  databricks_spark_job_name?: string | null;
  databricks_spark_job_id?: number | null;
};

export interface Destination {
  dst: string;
  name: string;
  config_data: Record<string, unknown>;
  fields?: FieldConfig[]; // Fields with read_only property from backend in edit mode
  is_file_based?: boolean;
  supports_notification_groups?: boolean;
}

export type DestinationSaveResponse = DatabricksSparkJobProvisionOutcome & {
  message?: string;
  auth_url?: string;
};

type AuditUser = {
  user_id?: number;
  email?: string;
  first_name?: string | null;
  last_name?: string | null;
};

export type DestinationTableItem = {
  dst_config_id: number;
  name: string;
  dst: string;
  created_at: string;
  updated_at: string;
  modified_at?: string | null;
  cmp: number;
  is_active: boolean;
  created_by?: AuditUser | string | null;
  updated_by?: AuditUser | string | null;
  modified_by?: AuditUser | string | null;
  created_by_name?: string | null;
  updated_by_name?: string | null;
  modified_by_name?: string | null;
  is_file_based?: boolean;
  supports_notification_groups?: boolean;
};
