import { type FieldConfig } from "./form";

export type ConnectorStatus = "A" | "P" | "E" | "S" | "B";

export type AuditUser = {
  user_id?: number;
  email?: string;
  first_name?: string | null;
  last_name?: string | null;
};

export interface CreateConnectionPayload {
  connection_name: string;
  destination_schema: string;
  form_data: Record<string, unknown>;
  destination_config_id?: number;
}

export interface ConnectorConfigResponse {
  source_schema: FieldConfig[]; // Array of fields with read_only property from backend in edit mode
  initial_data: Record<string, string>;
  destination_config: {
    name: string;
    dst: string;
  };
  fields?: FieldConfig[]; // Alias for source_schema (for backward compatibility)
}

export interface ConnectorTableItem {
  connection_id: number;
  connector_name: string;
  source_name: string;
  display_name: string;
  dst_config_name: string;
  destination_name: string;
  status: ConnectorStatus;
  readable_time_frequency: string;
  last_synced_new: string;
  next_sync_time: string;
  connected_on: number;
  created_at?: string | number | null;
  modified_at?: string | number | null;
  migration_status: string;
  error_message?: string | null;
  created_by?: AuditUser | string | null;
  updated_by?: AuditUser | string | null;
  modified_by?: AuditUser | string | null;
  created_by_name?: string | null;
  updated_by_name?: string | null;
  modified_by_name?: string | null;
}

export interface Connector {
  connection_id: number;
  time_frequency: string;
  safety_interval: string;
  execution_order: string;
  sync_start_date: string | null;
  chunk_count: number;
  status: ConnectorStatus;
  readable_time_frequency: string;
  readable_safety_interval: string;
  effective_max_chunk?: number;
  min_count?: number;
  max_count?: number;
  dst_min_count: number;
  dst_max_count: number;
  source_name: string;
  destination_name: string;
  source_title: string;
  destination_title: string;
  company_name: string;
  target_database: string;
  target_schema: string;
  next_sync_time: string;
  is_reverse_etl: boolean;
  connected_on: number | string;
  created_at?: string | number | null;
  modified_at?: string | number | null;
  migration_status?: string;
  reloadingTables?: string[];
  setReloadingTables?: React.Dispatch<React.SetStateAction<string[]>>;
  disable_update_schema?: boolean;
  created_by?: AuditUser | string | null;
  updated_by?: AuditUser | string | null;
  modified_by?: AuditUser | string | null;
  created_by_name?: string | null;
  updated_by_name?: string | null;
  modified_by_name?: string | null;
  is_file_based?: boolean;
  supports_notification_groups?: boolean;
  root_folder?: string | null;
  display_name?: string;
  has_batches_in_pipeline?: boolean;
  batches_in_pipeline?: Array<{
    batch_id: number;
    batch_name: string;
    pipeline_id: number;
    pipeline_name: string;
  }>;
}

export interface ConnectorTabsProps {
  connector?: Connector;
}

export interface ConnectorSyncStats {
  daily_labels: string[];
  new_rec_values_daily: number[];
  mod_rec_values_daily: number[];
  del_rec_values_daily: number[];
  monthly_labels: string[];
  new_rec_values_monthly: number[];
  mod_rec_values_monthly: number[];
  del_rec_values_monthly: number[];
  readable_time_frequency: string;
  cmp_id: number;
}

export type ConnectorSettingsApiResponse = {
  connection_id: number;
  time_frequency: string;
  safety_interval: string;
  execution_order: string;
  sync_start_date: string | null;
  chunk_count: number;
  status: ConnectorStatus;
  readable_time_frequency: string;
  readable_safety_interval: string;
  effective_max_chunk?: number;
  min_count?: number;
  max_count?: number;
  dst_min_count: number;
  dst_max_count: number;
  source_name: string;
  destination_name: string;
  source_title: string;
  destination_title: string;
  company_name: string;
  migration_status?: string;
  created_at?: string | number | null;
  modified_at?: string | number | null;
  created_by?: AuditUser | string | null;
  updated_by?: AuditUser | string | null;
  modified_by?: AuditUser | string | null;
  created_by_name?: string | null;
  updated_by_name?: string | null;
  modified_by_name?: string | null;
  is_file_based?: boolean;
  supports_notification_groups?: boolean;
};

export interface ExcelHeaderStyle {
  fill?: string;
  font_color?: string;
  bold?: boolean;
  italic?: boolean;
  font_name?: string;
  font_size?: number;
  horizontal?: string;
  vertical?: string;
  wrap_text?: boolean;
}

export interface ExcelColumnStyle {
  column_name: string;
  width?: number;
  horizontal?: string;
  vertical?: string;
  number_format?: string;
}

export interface ExcelBodyStyle {
  font_name?: string;
  font_size?: number;
  font_color?: string;
  bold?: boolean;
  italic?: boolean;
  fill?: string;
  fill_color?: string;
  background_color?: string;
  banding_fill?: string;
  banding_fill_color?: string;
  banding_background_color?: string;
  banding_font_color?: string;
  banding_frequency?: number;
}
export interface ExcelSortColumn {
  column_name: string;
  direction: "asc" | "desc";
}
export interface ExcelOptions {
  sheet_name?: string;
  auto_filter?: boolean;
  freeze_panes?: string;
  freeze_header?: boolean;
  auto_width?: boolean;
  date_format?: string;
  datetime_format?: string;
  time_format?: string;
  header_style?: ExcelHeaderStyle;
  sheet_header_style?: ExcelHeaderStyle;
  sheet_header?: string;
  sheet_header_row_span?: number;
  sheet_header_enabled?: boolean;
  hidden_columns?: string[];
  column_styles?: ExcelColumnStyle[];
  body_style?: ExcelBodyStyle;
  sort_columns?: ExcelSortColumn[];
}

export interface ExcelDifferentialStyle {
  fill?: string;
  fill_color?: string;
  background_color?: string;
  font_color?: string;
  bold?: boolean;
  italic?: boolean;
  horizontal?: string;
  vertical?: string;
  wrap_text?: boolean;
}

export interface ExcelConditionItem {
  id?: string;
  logic?: "and" | "or" | "and_not" | "or_not";
  column: string;
  operator: string;
  value: string;
  value2?: string;
  open_paren?: string;
  close_paren?: string;
}

export interface ExcelConditionalFormat {
  type: string;
  range?: string;
  column_name?: string;
  target_columns?: string[];
  condition_column_name?: string;
  conditions?: ExcelConditionItem[];
  highlight_scope?:
    | "cell"
    | "entire_row"
    | "entire_column"
    | "selected_columns"
    | string;
  formula?: string | string[];
  stop_if_true?: boolean;
  operator?: string;
  text?: string;
  rank?: number;
  percent?: boolean;
  bottom?: boolean;
  above_average?: boolean;
  below_average?: boolean;
  equal_average?: boolean;
  time_period?: string;
  colors?: string[];
  color?: string;
  show_value?: boolean;
  icon_style?: string;
  value_type?: string;
  values?: (number | string)[];
  start_type?: string;
  start_value?: string | number;
  end_type?: string;
  end_value?: string | number;
  reverse?: boolean;
  style?: ExcelDifferentialStyle;
}

export type FilenameDateFormat = "yyyy_mm_dd" | "dd_mm_yyyy" | "mm_dd_yyyy";

export interface TableFieldInfo {
  edm_type?: string;
  data_type?: string;
  filter_type?: "boolean" | "numeric" | "datetime" | "time" | "string";
  filter_restriction?: string | null;
  filterable?: boolean;
  display_format?: string;
  displayFormat?: string;
  precision?: number | string;
  nullable?: boolean;
  max_length?: number;
  [key: string]: unknown;
}

export type ConnectorTable = {
  table: string;
  selected: boolean;
  sequence: number | null;
  is_delta: boolean;
  service_name?: string | null;
  table_fields: Record<string, TableFieldInfo | string>;
  /** Present when listing tables from connection API / SourceTableSelection sync metadata */
  last_synced?: string | null;
  selected_fields?: string[] | null;
  output_file_name?: string | null;
  target_folder?: string | null;
  file_format?: "csv" | "json" | "parquet" | "excel" | string | null;
  csv_delimiter?: string | null;
  csv_quote_char?: string | null;
  add_utc_timestamp?: boolean | null;
  filename_date_format?: FilenameDateFormat | null;
  load_method?: string | null;
  delete_and_load?: boolean | null;
  partition_delta_by_date?: boolean | null;
  compression_method?: string | null;
  notification_email_group_ids?: number[] | null;
  email_template_id?: number | string | null;
  email_template?: number | string | { id?: number | string | null } | null;
  email_custom_fields?: {
    subject?: string;
    subject_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_fields?: string[];
    greeting_name?: string;
    greeting_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_content?: string;
    body_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    team_name?: string;
    team_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
  } | null;
  excel_sheet_name?: string | null;
  excel_options?: ExcelOptions | null;
  excel_conditional_formats?: ExcelConditionalFormat[] | null;
  // SAP OData ETL lifecycle control fields
  first_sync_timestamp?: string | null;
  initial_completed_flag?: boolean | null;
  load_method_locked?: boolean | null;
  last_delta_run_timestamp?: string | null;
  row_filter?: RowFilterConfig | null;
  row_filter_config?: RowFilterConfig | null;
  transform_summary?: TransformSummary | null;
};

export type DestinationNameSource = "DEFAULT" | "JSON" | "MANUAL";

export type TransformSummary = {
  has_mapping: boolean;
  has_script: boolean;
  version: number | null;
  badge: string | null;
  is_active: boolean;
  entry_point?: string | null;
  run_order?: string | null;
  destination_table_name?: string | null;
  destination_name_source?: DestinationNameSource | null;
  json_table_name?: string | null;
};

export type MappingTableRow = {
  sap_column: string | null;
  sap_columns?: string[];
  target_column: string | null;
  renamed: boolean;
  status: string;
  type_label?: string;
  source_type?: Record<string, unknown> | null;
  target_type?: Record<string, unknown> | null;
  constant_value?: unknown;
  added_by_user?: boolean;
};

export type EntityTransformDetail = {
  id: number;
  version: number;
  is_active: boolean;
  status?: "draft" | "validated";
  service_name: string;
  entity_name: string;
  table_name: string;
  mapping_json: Record<string, unknown> | null;
  mapping_overrides: Record<string, unknown>;
  script_text: string | null;
  script_sha256: string;
  entry_point: string;
  run_order: string;
  row_filter: Record<string, unknown> | null;
  json_table_name?: string | null;
  destination_table_name?: string | null;
  destination_name_source?: DestinationNameSource | null;
  previous_destination_table_name?: string | null;
  destination_rename_mode?: string | null;
  default_display_name?: string | null;
  created_at: string | null;
  badge: string | null;
  save_meta?: Record<string, unknown>;
};

export type DestinationPreview = {
  json_table_name?: string | null;
  destination_table_name?: string | null;
  destination_name_source?: DestinationNameSource | null;
  default_display_name?: string | null;
  default_destination_table_name?: string | null;
  warnings?: string[];
  valid?: boolean;
  collision?: {
    entity_label: string;
    connector_name?: string;
    entity_name: string;
    table_name: string;
    destination_table_name: string;
  } | null;
  existing_snowflake_table?: boolean;
  error?: string | null;
};

export type TransformValidateResponse = {
  valid: boolean;
  errors: Array<{
    path?: string;
    message: string;
    type?: string;
    line?: number;
    traceback?: string;
  }>;
  warnings: Array<{ type: string; message: string }>;
  checklist: Array<{
    id: string;
    label: string;
    passed: boolean;
    line?: number;
    message?: string;
    all_errors?: Array<{ line?: number; message?: string }>;
  }>;
  entry_points?: string[];
  counts?: { mapped: number; not_selected: number; from_script: number };
  mapping_table?: MappingTableRow[];
  row_filter_config?: RowFilterConfig | null;
  destination?: DestinationPreview | null;
  draft?: EntityTransformDetail | null;
  published?: EntityTransformDetail | null;
  publish_error?: string;
  requires_rename_confirmation?: boolean;
};

export type TransformDryRunResponse = {
  valid?: boolean;
  rows_in: number;
  rows_out: number;
  columns_out?: string[];
  output_columns?: string[];
  input_columns?: string[];
  expected_columns?: string[];
  warnings?: Array<{ type: string; message: string }>;
  errors?: Array<{
    type: string;
    message: string;
    line?: number;
    traceback?: string;
  }>;
  checklist?: Array<{
    id: string;
    label: string;
    passed: boolean;
    message?: string;
    line?: number;
  }>;
  entry_points?: string[];
  schema_check?: { passed: boolean; column_count: number };
  counts?: {
    mapped: number;
    not_selected: number;
    from_script: number;
    status?: Record<string, number>;
  };
  mapping_table?: MappingTableRow[];
  input_preview?: Record<string, unknown>[];
  output_preview?: Record<string, unknown>[];
  preview_rows?: Record<string, unknown>[];
  metadata?: Record<string, unknown>;
  row_filter_config?: RowFilterConfig | null;
};

export type ConnectorTablesResponse = {
  tables: ConnectorTable[];
  pagination_limit?: number;
};

export interface ConnectorSelectedTable {
  tbl_id: number;
  table: string;
  sequence: number;
  status: "in_progress" | "completed" | "failed" | null;
  output_file_name?: string | null;
  target_folder?: string | null;
  file_format?: "csv" | "json" | "parquet" | "excel" | string | null;
  csv_delimiter?: string | null;
  csv_quote_char?: string | null;
  add_utc_timestamp?: boolean | null;
  filename_date_format?: FilenameDateFormat | null;
  notification_email_group_ids?: number[] | null;
  email_custom_fields?: {
    subject?: string;
    subject_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_fields?: string[];
    greeting_name?: string;
    greeting_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_content?: string;
    body_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    team_name?: string;
    team_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
  } | null;
  excel_sheet_name?: string | null;
  excel_options?: ExcelOptions | null;
  excel_conditional_formats?: ExcelConditionalFormat[] | null;
}

// ------------------ Connector Activity Types ------------------

export type Status = "S" | "W" | "E" | "P" | "I";

export type ConnectorActivityLog = {
  message: string;
  user?: string;
  user_name?: string;
  timestamp: string;
  status: Status;
  session_id: number | null;
  is_clickable?: boolean;
  migration_id?: number | null;
  log_id?: number;
  log_type?: string;
  ui_state?: string;
  trigger_type?: string;
  batch_id?: number | null;
  batch_name?: string | null;
  batch_names?: string[];
  pipeline_run_id?: number | null;
  pipeline_id?: number | null;
  pipeline_name?: string | null;
};

export type MigrationRecord = {
  table_name: string;
  status: Status;
  timestamp: string;
  job_message: string;
};

export type ConnectorActivityResponse = {
  logs: ConnectorActivityLog[];
  migration_records: MigrationRecord[];
};

export interface TableChange {
  table: string;
  action: string;
  timestamp: string;
  changed_by: string;
  sequence?: number;
  old_sequence?: number;
  new_sequence?: number;
}

export interface ConnectorActivityDetailResponse {
  job_name?: string;
  migration_session_id?: number;
  connection_id?: number;
  overall_status?: string;
  overall_has_warning?: boolean;
  overall_warning_message?: string | null;
  job_level_message?: string | null;
  /** base_merged: in-progress count is cumulative rows in destination (Databricks). */
  progress_count_source?: "base_merged" | "staging";
  tables?: {
    table_name: string;
    status: string;
    status_icon?: string;
    start_time: string | null;
    end_time: string | null;
    mod_rec?: number;
    del_rec?: number;
    new_rec?: number;
    record_count?: number;
    duration?: string | null;
    migration_record_id?: number;
    message?: string;
    error_message?: string;
    staging_records_count?: number;
    already_mapped?: boolean;
    table_name_locked?: boolean;
    mapped_table?: string;
    locked_table_name?: string | null;
  }[];
  total_tables?: number;
  logs?: {
    table: string;
    status: string;
    timestamp: string;
    message: string;
  }[];
  // Fields for table selection logs
  log_id?: number;
  changes?: TableChange[];
  type?: string;
  summary?: string;
  user?: string | null;
}

export interface SchemaStatusResponse {
  is_in_progress: boolean;
  current_job: string | null;
  message?: string;
  tables_fetched?: number;
  total_tables?: number;
}

export type ReverseSchemaResponse = {
  source_tables?: ConnectorTable[];
  destination_tables?: ConnectorTable[];
  tables?: ConnectorTable[];
};

export interface BasicFilterCondition {
  column?: string;
  field?: string;
  operator: string;
  value: string | number | boolean | (string | number | boolean)[];
  edm_type?: string;
}

export interface GroupFilterCondition {
  logic: "and" | "or";
  operator?: "and" | "or";
  conditions: FilterNode[];
}

export interface NotFilterCondition {
  not: FilterNode;
}

export type FilterNode =
  | BasicFilterCondition
  | GroupFilterCondition
  | NotFilterCondition;

export interface FilterCondition {
  column?: string;
  field?: string;
  operator?: string;
  value?: string | number | boolean | (string | number | boolean)[];
  edm_type?: string;
  logic?: "and" | "or";
  conditions?: FilterNode[];
  not?: FilterNode;
}

export interface RowFilterConfig {
  logic?: "and" | "or";
  operator?: "and" | "or";
  conditions: FilterNode[];
}

// ------------------ Migration Batches ------------------

export type BatchStatus = "active" | "paused";
export type BatchExecutionOrder = "parallel" | "sequential";
export type BatchScheduleType = "manual" | "interval" | "cron";
export type BatchCronType = "daily" | "weekdays" | "weekly" | "monthly";

export interface BatchScheduleConfig {
  cron_type?: BatchCronType;
  hour?: number;
  minute?: number;
  timezone?: string;
  day_of_week?: string;
  days_of_week?: string[];
  day?: number;
  interval_unit?: string;
  interval_value?: number;
  monthly_mode?: string;
  monthly_week?: string;
  monthly_weekday?: string;
  monthly_day_cron?: string;
  recurrence_pattern?: string;
}

export interface BatchTable {
  table_name: string;
  sequence: number;
  last_synced?: string | null;
}

export interface MigrationBatch {
  id: number;
  name: string;
  time_frequency: string;
  execution_order: BatchExecutionOrder;
  schedule_type?: BatchScheduleType;
  schedule_config?: BatchScheduleConfig;
  sync_start_date: string | null;
  status: BatchStatus;
  tables: BatchTable[];
  table_count: number;
  readable_time_frequency?: string;
  next_sync_time?: string | null;
  in_pipeline?: boolean;
  pipeline_id?: number | null;
  pipeline_name?: string | null;
}

export interface UnassignedTable {
  table_name: string;
  sequence: number;
  last_synced?: string | null;
  /** Reverse ETL: destination table this source was mapped to (UI only). */
  mapped_destination?: string;
}

export interface FetchBatchesResponse {
  batches: MigrationBatch[];
  unassigned_tables: UnassignedTable[];
}

export interface CreateBatchPayload {
  name?: string;
  time_frequency: string | number;
  execution_order: BatchExecutionOrder;
  schedule_type?: BatchScheduleType;
  schedule_config?: BatchScheduleConfig;
  sync_start_date?: string | null;
}

export interface UpdateBatchPayload {
  name?: string;
  time_frequency?: string | number;
  execution_order?: BatchExecutionOrder;
  schedule_type?: BatchScheduleType;
  schedule_config?: BatchScheduleConfig;
  sync_start_date?: string | null;
  status?: BatchStatus;
}

export interface AssignTablesPayload {
  tables: string[];
}

export interface AssignTableConflict {
  table_name: string;
  batch_name: string;
}

export interface AssignTablesErrorResponse {
  conflicts?: AssignTableConflict[];
  detail?: string;
}
