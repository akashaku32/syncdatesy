import { useEffect, useMemo, useState } from "react";

import {
  ActionBar,
  Box,
  Button,
  Checkbox,
  Flex,
  Grid,
  IconButton,
  Input,
  InputGroup,
  Menu,
  Portal,
  Text,
} from "@chakra-ui/react";

import { IoMdMail, IoMdOptions, IoMdPlay, IoMdSettings } from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";
import { PiKeyFill } from "react-icons/pi";
import { TbTransform } from "react-icons/tb";

import Pagination from "@/components/shared/Pagination";
import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import { type ReverseSchemaResponse } from "@/queryOptions/connector/reverseSchema/useFetchReverseSchema";
import { usePagination } from "@/queryOptions/connector/schema/usePagination";
import useUpdateSelectedTables from "@/queryOptions/connector/schema/useUpdateSelectedTables";
import { useFetchDestinationById } from "@/queryOptions/destination/useFetchDestinationById";
import { useFetchDestinationListByPage } from "@/queryOptions/destination/useFetchDestinationListByPage";
import {
  type ExportConfigResponse,
  useFetchExportConfig,
} from "@/queryOptions/destination/useFetchExportConfig";
import useFetchEmailGroups from "@/queryOptions/emailGroups/useFetchEmailGroups";
import {
  type Connector,
  type ConnectorTable,
  type ExcelConditionalFormat,
  type ExcelOptions,
  type FilenameDateFormat,
  type UnassignedTable,
} from "@/types/connectors";

import BatchGroupedPanel from "../../../Schema/Batches/BatchGroupedPanel";
import { isConnectorTableMarkedSelected } from "../../../Schema/schemaSelection";
import { isPrimaryKey } from "../../utils/validation";
import EmailGroupSelectionModal, {
  type EmailCustomFieldsPayload,
} from "./EmailGroupSelectionModalNew";
import { DEFAULT_EXCEL_OPTIONS, cleanRulePayload } from "./ExcelSettings";
import FieldSelectionModal from "./FieldSelectionModal";
import FileExportTransformModal from "./FileExportTransformModal";
import TableExportSettingsModal from "./TableExportSettingsModal";
import useUpdateSelectedFields from "./hooks/useUpdateSelectedFields";
import useUpdateTableEmailGroups from "./hooks/useUpdateTableEmailGroups";
import useUpdateTableExportSettings from "./hooks/useUpdateTableExportSettings";
import { useQueryClient } from "@tanstack/react-query";

type FileFormat = "csv" | "json" | "parquet" | "excel";

type TableExportSetting = {
  output_file_name: string;
  target_folder: string;
  file_format: FileFormat;
  csv_delimiter?: string;
  csv_quote_char?: string;
  add_utc_timestamp: boolean;
  filename_date_format: FilenameDateFormat | null;
  notification_email_group_ids?: number[];
  email_template_id?: number | string | null;
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
  };
  excel_sheet_name?: string;
  excel_options?: ExcelOptions;
  excel_conditional_formats?: ExcelConditionalFormat[];
};

type TableExportDefaults = Omit<TableExportSetting, "output_file_name">;

interface SnowflakeFileExportSchemaProps {
  connector: Connector;
  reverseSchemaData: ReverseSchemaResponse | null;
  isDisabled: boolean;
  tableToBatchName?: Map<string, string>;
  onSelectionChange?: (_tables: string[], _isDirty: boolean) => void;
  pendingUnassignedTables?: UnassignedTable[];
}

const ITEMS_PER_PAGE = 50;

const DEFAULT_TABLE_SETTINGS: TableExportDefaults = {
  target_folder: "",
  file_format: "csv",
  csv_delimiter: ",",
  csv_quote_char: '"',
  add_utc_timestamp: true,
  filename_date_format: null,
  notification_email_group_ids: [],
  email_template_id: null,
};

const DESTINATION_FOLDER_FIELD_PRIORITY = [
  "folder_name",
  "root_folder",
  "target_folder",
  "directory_path",
  "container_name",
];

const resolveDestinationFolder = (config?: Record<string, unknown>) => {
  if (!config) return "";

  for (const field of DESTINATION_FOLDER_FIELD_PRIORITY) {
    const value = config[field];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  const genericFolderEntry = Object.entries(config).find(([field, value]) => {
    const normalizedField = field.toLowerCase();
    return (
      typeof value === "string" &&
      value.trim() !== "" &&
      !normalizedField.endsWith("_id") &&
      (normalizedField.includes("folder") ||
        normalizedField.includes("directory"))
    );
  });

  return typeof genericFolderEntry?.[1] === "string"
    ? genericFolderEntry[1].trim()
    : "";
};

const normalizeFilenameDateFormat = (
  value: unknown,
): FilenameDateFormat | null => {
  switch (value) {
    case null:
    case undefined:
    case "current":
      return null;
    case "yyyy_mm_dd":
    case "yyyy-mm-dd":
    case "yy-mm-dd":
      return "yyyy_mm_dd";
    case "dd_mm_yyyy":
    case "dd-mm-yyyy":
    case "dd-mm-yy":
      return "dd_mm_yyyy";
    case "mm_dd_yyyy":
    case "mm-dd-yyyy":
    case "mm-dd-yy":
      return "mm_dd_yyyy";
    default:
      return DEFAULT_TABLE_SETTINGS.filename_date_format;
  }
};

const isValidFileFormat = (value: unknown): value is FileFormat =>
  value === "csv" ||
  value === "json" ||
  value === "parquet" ||
  value === "excel";

function safeParseJson<T>(val: unknown): T | undefined {
  if (typeof val === "string") {
    try {
      return JSON.parse(val) as T;
    } catch {
      return undefined;
    }
  }
  return val as T;
}

const sanitizeExcelOptions = (
  opts?: ExcelOptions,
  defaultHeaderName?: string,
): ExcelOptions | undefined => {
  if (!opts) return undefined;
  const nextOpts = { ...opts };
  if (nextOpts.sheet_header_enabled === false) {
    delete nextOpts.sheet_header;
    delete nextOpts.sheet_header_style;
    delete nextOpts.sheet_header_row_span;
    delete (nextOpts as Record<string, unknown>).sheet_header_title;
    delete (nextOpts as Record<string, unknown>).sheet_header_merge_rows;
  } else {
    nextOpts.sheet_header =
      nextOpts.sheet_header?.trim() || defaultHeaderName || "";
  }
  return nextOpts;
};

const extractEmailTemplateId = (
  raw?: Partial<ConnectorTable>,
): number | string | null => {
  if (!raw) return null;
  if (
    typeof raw.email_template_id === "number" ||
    (typeof raw.email_template_id === "string" &&
      raw.email_template_id.trim() !== "")
  ) {
    return raw.email_template_id;
  }
  if (
    typeof raw.email_template === "number" ||
    (typeof raw.email_template === "string" && raw.email_template.trim() !== "")
  ) {
    return raw.email_template;
  }
  if (
    typeof raw.email_template === "object" &&
    raw.email_template !== null &&
    "id" in raw.email_template
  ) {
    const tmplId = (raw.email_template as { id?: number | string | null }).id;
    if (
      typeof tmplId === "number" ||
      (typeof tmplId === "string" && tmplId.trim() !== "")
    ) {
      return tmplId;
    }
  }
  return null;
};

const normalizeTableSetting = (
  tableName: string,
  raw?: Partial<ConnectorTable>,
  exportConfig?: ExportConfigResponse,
  defaultTargetFolder = "",
): TableExportSetting => {
  const defaultFormat = exportConfig?.destination?.default_format || "csv";
  const defaultCsvDelimiter =
    exportConfig?.destination?.csv_defaults?.delimiter || ",";
  const defaultCsvQuoteChar =
    exportConfig?.destination?.csv_defaults?.quote_char || '"';

  return {
    output_file_name: raw?.output_file_name || tableName,
    target_folder: raw?.target_folder?.trim() || defaultTargetFolder,
    file_format: isValidFileFormat(raw?.file_format)
      ? raw.file_format
      : isValidFileFormat(defaultFormat)
        ? defaultFormat
        : "csv",
    csv_delimiter: raw?.csv_delimiter || defaultCsvDelimiter,
    csv_quote_char: raw?.csv_quote_char || defaultCsvQuoteChar,
    add_utc_timestamp:
      typeof raw?.add_utc_timestamp === "boolean"
        ? raw.add_utc_timestamp
        : DEFAULT_TABLE_SETTINGS.add_utc_timestamp,
    filename_date_format: normalizeFilenameDateFormat(
      raw?.filename_date_format,
    ),
    notification_email_group_ids: Array.isArray(
      raw?.notification_email_group_ids,
    )
      ? raw.notification_email_group_ids
      : [],
    email_template_id: extractEmailTemplateId(raw),
    email_custom_fields: raw?.email_custom_fields
      ? {
          subject:
            raw.email_custom_fields.subject !== undefined &&
            raw.email_custom_fields.subject !== null
              ? raw.email_custom_fields.subject
              : "",
          subject_styles: raw.email_custom_fields.subject_styles
            ? {
                bold: !!raw.email_custom_fields.subject_styles.bold,
                italic: !!raw.email_custom_fields.subject_styles.italic,
                color:
                  raw.email_custom_fields.subject_styles.color || undefined,
                font_family:
                  raw.email_custom_fields.subject_styles.font_family ||
                  undefined,
                font_size:
                  raw.email_custom_fields.subject_styles.font_size || undefined,
              }
            : undefined,
          body_fields: Array.isArray(raw.email_custom_fields.body_fields)
            ? raw.email_custom_fields.body_fields
            : undefined,
          greeting_name:
            raw.email_custom_fields.greeting_name !== undefined &&
            raw.email_custom_fields.greeting_name !== null
              ? raw.email_custom_fields.greeting_name
              : "",
          greeting_styles: raw.email_custom_fields.greeting_styles
            ? {
                bold: !!raw.email_custom_fields.greeting_styles.bold,
                italic: !!raw.email_custom_fields.greeting_styles.italic,
                color:
                  raw.email_custom_fields.greeting_styles.color || undefined,
                font_family:
                  raw.email_custom_fields.greeting_styles.font_family ||
                  undefined,
                font_size:
                  raw.email_custom_fields.greeting_styles.font_size ||
                  undefined,
              }
            : undefined,
          body_content:
            raw.email_custom_fields.body_content !== undefined &&
            raw.email_custom_fields.body_content !== null
              ? raw.email_custom_fields.body_content
              : "",
          body_styles: raw.email_custom_fields.body_styles
            ? {
                bold: !!raw.email_custom_fields.body_styles.bold,
                italic: !!raw.email_custom_fields.body_styles.italic,
                color: raw.email_custom_fields.body_styles.color || undefined,
                font_family:
                  raw.email_custom_fields.body_styles.font_family || undefined,
                font_size:
                  raw.email_custom_fields.body_styles.font_size || undefined,
              }
            : undefined,
          team_name:
            raw.email_custom_fields.team_name !== undefined &&
            raw.email_custom_fields.team_name !== null
              ? raw.email_custom_fields.team_name
              : "Thanks & Regards,",
          team_styles: raw.email_custom_fields.team_styles
            ? {
                bold: !!raw.email_custom_fields.team_styles.bold,
                italic: !!raw.email_custom_fields.team_styles.italic,
                color: raw.email_custom_fields.team_styles.color || undefined,
                font_family:
                  raw.email_custom_fields.team_styles.font_family || undefined,
                font_size:
                  raw.email_custom_fields.team_styles.font_size || undefined,
              }
            : undefined,
          styles: raw.email_custom_fields.styles
            ? {
                bold: !!raw.email_custom_fields.styles.bold,
                italic: !!raw.email_custom_fields.styles.italic,
                color:
                  raw.email_custom_fields.styles.color !== undefined &&
                  raw.email_custom_fields.styles.color !== null
                    ? raw.email_custom_fields.styles.color
                    : undefined,
                font_family:
                  raw.email_custom_fields.styles.font_family !== undefined &&
                  raw.email_custom_fields.styles.font_family !== null
                    ? raw.email_custom_fields.styles.font_family
                    : undefined,
                font_size:
                  raw.email_custom_fields.styles.font_size !== undefined &&
                  raw.email_custom_fields.styles.font_size !== null
                    ? raw.email_custom_fields.styles.font_size
                    : undefined,
              }
            : undefined,
        }
      : undefined,
    excel_sheet_name: raw?.excel_sheet_name || undefined,
    excel_options:
      sanitizeExcelOptions(safeParseJson<ExcelOptions>(raw?.excel_options)) ||
      undefined,
    excel_conditional_formats:
      safeParseJson<ExcelConditionalFormat[]>(
        raw?.excel_conditional_formats,
      )?.map((r) => cleanRulePayload(r)) || undefined,
  };
};

const sanitizeTableExportSetting = (
  setting: TableExportSetting,
  isEmailSupported: boolean,
  defaultSheetName?: string,
): TableExportSetting => {
  const base: TableExportSetting = {
    output_file_name: setting.output_file_name.trim(),
    target_folder: setting.target_folder.trim(),
    file_format: setting.file_format,
    add_utc_timestamp: setting.add_utc_timestamp,
    filename_date_format: normalizeFilenameDateFormat(
      setting.filename_date_format,
    ),
    ...(isEmailSupported
      ? {
          notification_email_group_ids:
            setting.notification_email_group_ids || [],
          email_template_id: setting.email_template_id ?? null,
          email_custom_fields: setting.email_custom_fields || undefined,
        }
      : {}),
  };

  if (setting.file_format === "csv") {
    base.csv_delimiter = setting.csv_delimiter || ",";
    base.csv_quote_char = setting.csv_quote_char || '"';
  } else if (setting.file_format === "excel") {
    base.excel_sheet_name =
      setting.excel_sheet_name || setting.excel_options?.sheet_name || "Sheet1";
    base.excel_options = sanitizeExcelOptions(
      setting.excel_options || {
        ...DEFAULT_EXCEL_OPTIONS,
        sheet_name: base.excel_sheet_name,
      },
      defaultSheetName || setting.output_file_name,
    );
    base.excel_conditional_formats =
      setting.excel_conditional_formats?.map((r) => cleanRulePayload(r)) || [];
  }

  return base;
};

const hasApiTableExportSettings = (raw?: Partial<ConnectorTable>) =>
  !!raw &&
  ((raw.output_file_name !== null && raw.output_file_name !== undefined) ||
    (raw.target_folder !== null && raw.target_folder !== undefined) ||
    (raw.file_format !== null && raw.file_format !== undefined));

const getReusableTableDefaults = (
  raw?: Partial<TableExportSetting>,
  exportConfig?: ExportConfigResponse,
): TableExportDefaults => {
  const defaultFormat = exportConfig?.destination?.default_format || "csv";
  const defaultCsvDelimiter =
    exportConfig?.destination?.csv_defaults?.delimiter || ",";
  const defaultCsvQuoteChar =
    exportConfig?.destination?.csv_defaults?.quote_char || '"';

  return {
    target_folder:
      raw?.target_folder?.trim() ?? DEFAULT_TABLE_SETTINGS.target_folder,
    file_format: isValidFileFormat(raw?.file_format)
      ? raw.file_format
      : isValidFileFormat(defaultFormat)
        ? defaultFormat
        : "csv",
    csv_delimiter: raw?.csv_delimiter || defaultCsvDelimiter,
    csv_quote_char: raw?.csv_quote_char || defaultCsvQuoteChar,
    add_utc_timestamp:
      typeof raw?.add_utc_timestamp === "boolean"
        ? raw.add_utc_timestamp
        : DEFAULT_TABLE_SETTINGS.add_utc_timestamp,
    filename_date_format: normalizeFilenameDateFormat(
      raw?.filename_date_format,
    ),
    notification_email_group_ids: [],
    email_template_id: null,
  };
};

const validateTargetFolder = (_value: string, _connector?: Connector) => "";

const SnowflakeFileExportSchema = ({
  connector,
  reverseSchemaData,
  isDisabled,
  tableToBatchName,
  onSelectionChange,
  pendingUnassignedTables = [],
}: SnowflakeFileExportSchemaProps) => {
  const queryClient = useQueryClient();

  const { mutate: updateSelectedTables, isPending: isSavingSelection } =
    useUpdateSelectedTables({
      connectorId: connector.connection_id,
    });

  const { data: emailGroups = [] } = useFetchEmailGroups();
  const { data: exportConfig } = useFetchExportConfig(
    connector.destination_name,
  );

  const { data: destinationList } = useFetchDestinationListByPage({
    page: 1,
    size: 10,
    searchTerm: connector.destination_title,
  });
  const connectedDestination = destinationList?.content.find(
    ({ name }) =>
      name.trim().toLowerCase() ===
      connector.destination_title.trim().toLowerCase(),
  );
  const { data: destinationDetails } = useFetchDestinationById(
    connectedDestination?.dst_config_id.toString() || "",
  );
  const destinationFolderName = resolveDestinationFolder(
    destinationDetails?.config_data,
  );
  const defaultTargetFolder =
    destinationFolderName || connector.root_folder?.trim() || "";

  const isEmailSupportedDestination = exportConfig?.destination
    ? exportConfig.destination.supports_notification_groups
    : !!connector.supports_notification_groups;

  const sourceTables = useMemo(
    () => reverseSchemaData?.source_tables || [],
    [reverseSchemaData?.source_tables],
  );

  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [tableExportSettings, setTableExportSettings] = useState<
    Record<string, TableExportSetting>
  >({});
  const [isSelectionDirty, setIsSelectionDirty] = useState(false);
  const [sourceSearch, setSourceSearch] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [activeTableForSettings, setActiveTableForSettings] = useState<
    string | null
  >(null);
  const [targetFolderErrors, setTargetFolderErrors] = useState<
    Record<string, string>
  >({});
  const [lastSavedDefaults, setLastSavedDefaults] =
    useState<TableExportDefaults>(DEFAULT_TABLE_SETTINGS);
  const [lastInteractedTable, setLastInteractedTable] = useState<string | null>(
    null,
  );
  const [fieldModalOpen, setFieldModalOpen] = useState(false);
  const [activeTableForFields, setActiveTableForFields] = useState<
    string | null
  >(null);
  const [transformModalOpen, setTransformModalOpen] = useState(false);
  const [activeTableForTransform, setActiveTableForTransform] = useState<
    string | null
  >(null);
  const [emailModalOpen, setEmailModalOpen] = useState(false);
  const [activeTableForEmail, setActiveTableForEmail] = useState<string | null>(
    null,
  );
  const [activeTableForCopy, setActiveTableForCopy] = useState<string | null>(
    null,
  );
  const [copyType, setCopyType] = useState<"email" | "export" | null>(null);
  const [selectedCopyTargets, setSelectedCopyTargets] = useState<string[]>([]);

  const { mutate: updateSelectedFields, isPending: isUpdatingFields } =
    useUpdateSelectedFields({
      connectorId: connector.connection_id,
    });

  const { mutate: updateTableEmailGroups, isPending: isUpdatingEmails } =
    useUpdateTableEmailGroups({
      connectorId: connector.connection_id,
    });

  const { mutate: updateTableExportSettings, isPending: isUpdatingSettings } =
    useUpdateTableExportSettings({
      connectorId: connector.connection_id,
    });

  const activeTableSettings = useMemo(() => {
    if (!activeTableForSettings) return null;
    return (
      tableExportSettings[activeTableForSettings] ||
      normalizeTableSetting(activeTableForSettings, undefined, exportConfig)
    );
  }, [activeTableForSettings, tableExportSettings, exportConfig]);

  const activeTableFields = useMemo(() => {
    if (!activeTableForSettings) return {};
    const tableData = sourceTables.find(
      (t) => t.table === activeTableForSettings,
    );
    return tableData?.table_fields || {};
  }, [activeTableForSettings, sourceTables]);

  const activeTableSelectedFields = useMemo(() => {
    if (!activeTableForSettings) return null;
    const tableData = sourceTables.find(
      (t) => t.table === activeTableForSettings,
    );
    return tableData?.selected_fields || null;
  }, [activeTableForSettings, sourceTables]);

  useEffect(() => {
    if (isSelectionDirty || isSavingSelection) return;

    const selectedFromSchema = sourceTables
      .filter(
        (item) =>
          isConnectorTableMarkedSelected(item.selected) ||
          tableToBatchName?.has(item.table.toLowerCase()),
      )
      .map((item) => item.table);
    const nextSelected = Array.from(new Set([...selectedFromSchema]));

    setSelectedTables((current) => {
      if (JSON.stringify(current) === JSON.stringify(nextSelected)) {
        return current;
      }
      return nextSelected;
    });

    setTableExportSettings((current) => {
      const nextSettings: Record<string, TableExportSetting> = {};
      nextSelected.forEach((tableName) => {
        const schemaRow = sourceTables.find((item) => item.table === tableName);
        nextSettings[tableName] = normalizeTableSetting(
          tableName,
          schemaRow,
          exportConfig,
          defaultTargetFolder,
        );
      });

      if (JSON.stringify(current) === JSON.stringify(nextSettings)) {
        return current;
      }
      return nextSettings;
    });

    const mostRecentSavedTable = [...nextSelected]
      .reverse()
      .find((tableName) => {
        const schemaRow = sourceTables.find((item) => item.table === tableName);
        return hasApiTableExportSettings(schemaRow);
      });
    if (mostRecentSavedTable) {
      const schemaRow = sourceTables.find(
        (item) => item.table === mostRecentSavedTable,
      );
      const nextDefaults = getReusableTableDefaults(
        normalizeTableSetting(
          mostRecentSavedTable,
          schemaRow,
          exportConfig,
          defaultTargetFolder,
        ),
        exportConfig,
      );
      setLastSavedDefaults((current) => {
        if (JSON.stringify(current) === JSON.stringify(nextDefaults)) {
          return current;
        }
        return nextDefaults;
      });
    }
    setIsSelectionDirty(false);
  }, [
    sourceTables,
    isSelectionDirty,
    isSavingSelection,
    exportConfig,
    tableToBatchName,
    defaultTargetFolder,
  ]);

  useEffect(() => {
    onSelectionChange?.(selectedTables, isSelectionDirty);
  }, [selectedTables, isSelectionDirty, onSelectionChange]);

  const filteredSourcePanelTables = useMemo(() => {
    const query = sourceSearch.trim().toLowerCase();
    const filtered = query
      ? sourceTables.filter((item) => item.table.toLowerCase().includes(query))
      : [...sourceTables];

    return filtered.sort((a, b) => a.table.localeCompare(b.table));
  }, [sourceSearch, sourceTables]);

  const selectedSourceTables = useMemo(() => {
    const selectedSet = new Set(selectedTables);
    return sourceTables.filter((item) => selectedSet.has(item.table));
  }, [sourceTables, selectedTables]);

  const {
    currentData: paginatedSourceTables,
    currentPage,
    totalPages,
    jumpToPage,
  } = usePagination({
    data: filteredSourcePanelTables,
    itemsPerPage: ITEMS_PER_PAGE,
  });

  useEffect(() => {
    jumpToPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceSearch]);

  const toggleTableSelection = (tableName: string) => {
    setSelectedTables((prev) => {
      if (prev.includes(tableName)) {
        setIsSelectionDirty(true);
        return prev.filter((name) => name !== tableName);
      }
      const schemaRow = sourceTables.find((item) => item.table === tableName);
      setTableExportSettings((current) => ({
        ...current,
        [tableName]:
          current[tableName] ||
          (hasApiTableExportSettings(schemaRow)
            ? normalizeTableSetting(tableName, schemaRow, exportConfig)
            : {
                output_file_name: tableName,
                ...lastSavedDefaults,
              }),
      }));
      setTargetFolderErrors((prev) => {
        const next = { ...prev };
        delete next[tableName];
        return next;
      });
      setLastInteractedTable(tableName);
      setIsSelectionDirty(true);
      return [...prev, tableName];
    });
  };

  const handleSaveSelection = () => {
    if (isDisabled) {
      toaster.warning({
        title: "Operation in progress",
        description: "Please wait for the current operation to complete.",
      });
      return;
    }

    const nextTargetFolderErrors = selectedTables.reduce<
      Record<string, string>
    >((acc, table) => {
      const settings =
        tableExportSettings[table] ||
        normalizeTableSetting(table, undefined, exportConfig);
      const error = validateTargetFolder(settings.target_folder, connector);
      if (error) {
        acc[table] = error;
      }
      return acc;
    }, {});

    if (Object.keys(nextTargetFolderErrors).length > 0) {
      setTargetFolderErrors(nextTargetFolderErrors);
      toaster.error({
        title: "Target folder is required",
        description:
          "Enter a target folder for each selected table before saving.",
      });
      return;
    }

    const truncateFlags = selectedTables.reduce<Record<string, boolean>>(
      (acc, table) => {
        acc[table] = false;
        return acc;
      },
      {},
    );

    const tableExportPayload = selectedTables.reduce<
      Record<string, TableExportSetting>
    >((acc, table) => {
      const row =
        tableExportSettings[table] ||
        normalizeTableSetting(table, undefined, exportConfig);
      acc[table] = sanitizeTableExportSetting(
        row,
        isEmailSupportedDestination,
        table,
      );
      return acc;
    }, {});

    updateSelectedTables(
      {
        selected_tables: selectedTables,
        truncate_flags: truncateFlags,
        table_export_settings: tableExportPayload,
      },
      {
        onSuccess: () => {
          toaster.success({ title: "Selection saved" });
          setIsSelectionDirty(false);
          setTargetFolderErrors({});
          const templateSourceTable =
            (lastInteractedTable &&
              tableExportPayload[lastInteractedTable] &&
              lastInteractedTable) ||
            selectedTables[selectedTables.length - 1];
          if (templateSourceTable) {
            setLastSavedDefaults(
              getReusableTableDefaults(
                tableExportPayload[templateSourceTable],
                exportConfig,
              ),
            );
          }
          queryClient.invalidateQueries({
            queryKey: ["ReverseSchema", connector.connection_id],
          });
        },
      },
    );
  };

  const handleSaveFields = (selectedFields: string[]) => {
    if (!activeTableForFields) return;
    updateSelectedFields(
      { tableName: activeTableForFields, selected_fields: selectedFields },
      {
        onSuccess: () => {
          toaster.success({ title: "Field selection updated" });
          setFieldModalOpen(false);
          queryClient.invalidateQueries({
            queryKey: ["ReverseSchema", connector.connection_id],
          });
        },
      },
    );
  };

  const handleSaveEmailGroups = (
    selectedGroupIds: number[],
    customFields?: EmailCustomFieldsPayload,
    emailTemplateId?: number | string | null,
  ) => {
    if (!activeTableForEmail) return;

    if (!selectedGroupIds || selectedGroupIds.length === 0) {
      toaster.error({ title: "Please select at least one email group" });
      return;
    }

    updateTableEmailGroups(
      {
        tableName: activeTableForEmail,
        notification_email_group_ids: selectedGroupIds,
        email_template_id: emailTemplateId ?? null,
        email_custom_fields: customFields,
      },
      {
        onSuccess: () => {
          toaster.success({ title: "Email settings updated" });
          setTableExportSettings((prev) => ({
            ...prev,
            [activeTableForEmail]: {
              ...(prev[activeTableForEmail] ||
                normalizeTableSetting(
                  activeTableForEmail,
                  undefined,
                  exportConfig,
                )),
              notification_email_group_ids: selectedGroupIds,
              email_template_id: emailTemplateId ?? null,
              email_custom_fields: customFields,
            },
          }));
          setEmailModalOpen(false);
          queryClient.invalidateQueries({
            queryKey: ["ReverseSchema", connector.connection_id],
          });
        },
      },
    );
  };

  const handleCopySettings = (targetTables: string[]) => {
    if (!activeTableForCopy) return;

    const sourceSettings =
      tableExportSettings[activeTableForCopy] ||
      normalizeTableSetting(activeTableForCopy, undefined, exportConfig);
    const updatedSettings = { ...tableExportSettings };

    if (copyType === "email") {
      const sourceEmailGroups =
        sourceSettings.notification_email_group_ids || [];
      const sourceEmailCustomFields =
        sourceSettings.email_custom_fields || undefined;
      const sourceEmailTemplateId = sourceSettings.email_template_id ?? null;
      targetTables.forEach((table) => {
        updatedSettings[table] = {
          ...(updatedSettings[table] ||
            normalizeTableSetting(table, undefined, exportConfig)),
          notification_email_group_ids: sourceEmailGroups,
          email_custom_fields: sourceEmailCustomFields,
          email_template_id: sourceEmailTemplateId,
        };
      });
    } else {
      targetTables.forEach((table) => {
        const currentTargetSettings =
          updatedSettings[table] ||
          normalizeTableSetting(table, undefined, exportConfig);

        const sourceExcelOpts = sourceSettings.excel_options;
        const targetExcelOpts = currentTargetSettings.excel_options;

        const mergedExcelOptions = sourceExcelOpts
          ? {
              ...sourceExcelOpts,
              sheet_name: targetExcelOpts?.sheet_name,
              sheet_header: targetExcelOpts?.sheet_header,
              hidden_columns: targetExcelOpts?.hidden_columns,
              column_styles: targetExcelOpts?.column_styles,
            }
          : undefined;

        updatedSettings[table] = {
          ...currentTargetSettings,
          target_folder: sourceSettings.target_folder,
          file_format: sourceSettings.file_format,
          csv_delimiter: sourceSettings.csv_delimiter,
          csv_quote_char: sourceSettings.csv_quote_char,
          add_utc_timestamp: sourceSettings.add_utc_timestamp,
          excel_sheet_name: currentTargetSettings.excel_sheet_name,
          excel_options: mergedExcelOptions,
          excel_conditional_formats:
            currentTargetSettings.excel_conditional_formats,
        };
      });
    }

    const truncateFlags = selectedTables.reduce<Record<string, boolean>>(
      (acc, table) => {
        acc[table] = false;
        return acc;
      },
      {},
    );

    const tableExportPayload = selectedTables.reduce<
      Record<string, TableExportSetting>
    >((acc, table) => {
      const row = updatedSettings[table] || normalizeTableSetting(table);
      acc[table] = sanitizeTableExportSetting(
        row,
        isEmailSupportedDestination,
        table,
      );
      return acc;
    }, {});

    updateSelectedTables(
      {
        selected_tables: selectedTables,
        truncate_flags: truncateFlags,
        table_export_settings: tableExportPayload,
      },
      {
        onSuccess: () => {
          toaster.success({
            title: "Settings copied successfully",
            description: `Copied ${
              copyType === "email" ? "email notifications" : "export settings"
            } from ${activeTableForCopy} to ${targetTables.length} table(s).`,
          });
          setTableExportSettings(updatedSettings);
          setActiveTableForCopy(null);
          setCopyType(null);
          setSelectedCopyTargets([]);
          queryClient.invalidateQueries({
            queryKey: ["ReverseSchema", connector.connection_id],
          });
        },
      },
    );
  };

  const activeTableData = useMemo(() => {
    if (!activeTableForFields) return null;
    return sourceTables.find((t) => t.table === activeTableForFields);
  }, [activeTableForFields, sourceTables]);

  const activeTransformTableItem = useMemo(() => {
    if (!activeTableForTransform) return null;
    return (
      sourceTables.find((t) => t.table === activeTableForTransform) || null
    );
  }, [activeTableForTransform, sourceTables]);

  const emailTableData = useMemo(() => {
    if (!activeTableForEmail) return null;
    return sourceTables.find((t) => t.table === activeTableForEmail);
  }, [activeTableForEmail, sourceTables]);

  const emailTableColumnsCount = useMemo(() => {
    if (!emailTableData) return undefined;
    return emailTableData.selected_fields
      ? emailTableData.selected_fields.length
      : Object.keys(emailTableData.table_fields || {}).length;
  }, [emailTableData]);

  return (
    <Flex direction="column" gap={4} w="100%" minW={0}>
      <Flex direction="column" gap={1} mr="auto" mt="-64px" w="100%" maxW="md">
        <InputGroup endElement={<MdSearch size={28} />}>
          <Input
            placeholder="Search table name"
            size="md"
            value={sourceSearch}
            onChange={(e) => {
              setSourceSearch(e.target.value);
              jumpToPage(1);
            }}
            disabled={!!activeTableForCopy}
          />
        </InputGroup>
      </Flex>
      <Grid templateColumns="1.2fr 1fr" gap={4} w="100%" alignItems="stretch">
        <Flex
          direction="column"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          padding={4}
          bgColor="white"
          minH={0}
          maxH="72vh"
          overflow="hidden"
        >
          <Flex
            mb={3}
            justifyContent="space-between"
            alignItems="center"
            flexShrink={0}
            pr={2}
          >
            <Flex gap={2} alignItems="center">
              {activeTableForCopy && <Box w="24px" />}
              <Text fontSize="sm" fontWeight="semibold">
                Source Tables
              </Text>
            </Flex>
            <Flex gap={3.5} alignItems="center">
              {activeTableForCopy && (
                <Flex justifyContent="center" w="36px" flexShrink={0}>
                  <Text fontSize="sm" fontWeight="semibold">
                    Paste
                  </Text>
                </Flex>
              )}
              <Flex justifyContent="center" w="60px" flexShrink={0}>
                <Text fontSize="sm" fontWeight="semibold">
                  Fields
                </Text>
              </Flex>
              <Flex justifyContent="center" w="76px" flexShrink={0}>
                <Text fontSize="sm" fontWeight="semibold">
                  Transform
                </Text>
              </Flex>
              <Flex justifyContent="center" w="66px" flexShrink={0}>
                <Text fontSize="sm" fontWeight="semibold">
                  Settings
                </Text>
              </Flex>
              {isEmailSupportedDestination && (
                <Flex justifyContent="center" w="50px" flexShrink={0}>
                  <Text fontSize="sm" fontWeight="semibold">
                    Email
                  </Text>
                </Flex>
              )}
              <Flex justifyContent="center" w="48px" flexShrink={0}>
                <Text fontSize="sm" fontWeight="semibold">
                  Select
                </Text>
              </Flex>
            </Flex>
          </Flex>

          {!filteredSourcePanelTables.length && (
            <Flex direction="column" alignItems="center" py={8}>
              <Text>No Source Tables available</Text>
            </Flex>
          )}

          {filteredSourcePanelTables.length > 0 && (
            <>
              <Flex
                direction="column"
                gap={2}
                flex="1"
                minH={0}
                overflowY="auto"
                pr={1}
              >
                {paginatedSourceTables.map((item, index) => {
                  const { table, table_fields } = item;
                  const isEven = index % 2 === 0;
                  const rowBg = isEven ? "gray.100" : "gray.50";
                  const isExpanded = !!expanded[table];
                  const isSelected = selectedTables.includes(table);
                  const batchName = tableToBatchName?.get(table.toLowerCase());

                  return (
                    <Flex
                      key={table}
                      backgroundColor={rowBg}
                      direction="column"
                      padding={2}
                      borderRadius={4}
                    >
                      <Grid
                        templateColumns="24px 1fr auto"
                        alignItems="center"
                        width="100%"
                        gap={2}
                      >
                        <Box>
                          <Box
                            onClick={(e) => {
                              if (activeTableForCopy) return;
                              e.stopPropagation();
                              setExpanded((prev) => ({
                                ...prev,
                                [table]: !prev[table],
                              }));
                            }}
                            cursor={
                              activeTableForCopy ? "not-allowed" : "pointer"
                            }
                          >
                            {isExpanded ? <IoCaretDownSharp /> : <IoMdPlay />}
                          </Box>
                        </Box>

                        <Box flex="1">
                          <Flex alignItems="center" gap={2}>
                            <Text
                              fontSize="sm"
                              cursor="pointer"
                              onClick={(e) => {
                                if (activeTableForCopy) return;
                                e.stopPropagation();
                                setExpanded((prev) => ({
                                  ...prev,
                                  [table]: !prev[table],
                                }));
                              }}
                            >
                              {table}
                            </Text>
                            {batchName && (
                              <Text
                                as="span"
                                fontSize="2xs"
                                fontWeight="semibold"
                                color="brand.700"
                                bgColor="brand.100"
                                px={1.5}
                                py={0.5}
                                borderRadius="sm"
                                maxW="120px"
                                truncate
                                title={batchName}
                              >
                                {batchName}
                              </Text>
                            )}
                            {item.transform_summary?.badge && (
                              <Text
                                as="span"
                                fontSize="2xs"
                                fontWeight="semibold"
                                color="brand.700"
                                bgColor="brand.100"
                                px={1.5}
                                py={0.5}
                                borderRadius="sm"
                                maxW="120px"
                                truncate
                                title={item.transform_summary.badge}
                              >
                                {item.transform_summary.badge}
                              </Text>
                            )}
                          </Flex>
                        </Box>
                        <Flex gap={3.5} alignItems="center" justifySelf="end">
                          {activeTableForCopy && (
                            <Flex
                              justifyContent="center"
                              w="36px"
                              flexShrink={0}
                            >
                              {table === activeTableForCopy ? (
                                <Flex
                                  w="16px"
                                  h="16px"
                                  borderWidth="1px"
                                  borderColor="gray.300"
                                  borderRadius="sm"
                                  alignItems="center"
                                  justifyContent="center"
                                  bg="gray.100"
                                >
                                  <Box
                                    w="6px"
                                    h="6px"
                                    borderRadius="full"
                                    bg="brand.500"
                                  />
                                </Flex>
                              ) : (
                                <Checkbox.Root
                                  colorPalette="brand"
                                  disabled={
                                    table === activeTableForCopy || !isSelected
                                  }
                                  checked={selectedCopyTargets.includes(table)}
                                  onCheckedChange={(details) => {
                                    if (details.checked === true) {
                                      setSelectedCopyTargets((prev) => [
                                        ...prev,
                                        table,
                                      ]);
                                    } else {
                                      setSelectedCopyTargets((prev) =>
                                        prev.filter((t) => t !== table),
                                      );
                                    }
                                  }}
                                >
                                  <Checkbox.HiddenInput />
                                  <Checkbox.Control />
                                </Checkbox.Root>
                              )}
                            </Flex>
                          )}
                          {(() => {
                            return (
                              <Flex
                                justifyContent="center"
                                w="60px"
                                flexShrink={0}
                              >
                                <Tooltip content="Select Export Fields">
                                  <IconButton
                                    size="xs"
                                    variant="ghost"
                                    colorPalette="brand"
                                    disabled={
                                      !!activeTableForCopy || !isSelected
                                    }
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setActiveTableForFields(table);
                                      setFieldModalOpen(true);
                                    }}
                                    borderRadius="full"
                                    height="22px"
                                    width="22px"
                                    minWidth="22px"
                                    flexShrink={0}
                                    fontSize="14px"
                                    boxShadow="none"
                                    bg="transparent"
                                    color="gray.500"
                                    borderColor="transparent"
                                    _hover={{
                                      bg: "brand.50",
                                      color: "brand.600",
                                    }}
                                    transition="all 0.2s"
                                  >
                                    <IoMdOptions />
                                  </IconButton>
                                </Tooltip>
                              </Flex>
                            );
                          })()}

                          {(() => {
                            const hasTransform = Boolean(
                              item.transform_summary?.badge ||
                                (item as { is_transform_active?: boolean })
                                  ?.is_transform_active,
                            );
                            return (
                              <Flex
                                justifyContent="center"
                                w="76px"
                                flexShrink={0}
                              >
                                <Tooltip content="Transform">
                                  <IconButton
                                    size="xs"
                                    variant={hasTransform ? "subtle" : "ghost"}
                                    colorPalette="brand"
                                    disabled={
                                      !!activeTableForCopy || !isSelected
                                    }
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setActiveTableForTransform(table);
                                      setTransformModalOpen(true);
                                    }}
                                    borderRadius="full"
                                    height="22px"
                                    width="22px"
                                    minWidth="22px"
                                    flexShrink={0}
                                    fontSize="14px"
                                    boxShadow="none"
                                    bg="transparent"
                                    color={
                                      hasTransform ? "brand.600" : "gray.500"
                                    }
                                    borderColor="transparent"
                                    _hover={{
                                      bg: "brand.50",
                                      color: "brand.600",
                                    }}
                                    transition="all 0.2s"
                                    aria-label="Transform"
                                  >
                                    <TbTransform />
                                  </IconButton>
                                </Tooltip>
                              </Flex>
                            );
                          })()}

                          {(() => {
                            const hasError = !!targetFolderErrors[table];
                            return (
                              <Flex
                                justifyContent="center"
                                w="66px"
                                flexShrink={0}
                              >
                                <Tooltip
                                  content={
                                    hasError
                                      ? targetFolderErrors[table]
                                      : "Export Settings"
                                  }
                                >
                                  <Menu.Root
                                    positioning={{ placement: "bottom-end" }}
                                  >
                                    <Menu.Trigger asChild>
                                      <IconButton
                                        size="xs"
                                        variant="ghost"
                                        colorPalette={
                                          hasError ? "red" : "brand"
                                        }
                                        disabled={
                                          !!activeTableForCopy || !isSelected
                                        }
                                        onClick={(e) => e.stopPropagation()}
                                        borderRadius="md"
                                        height="22px"
                                        px={1.5}
                                        minWidth="34px"
                                        flexShrink={0}
                                        boxShadow="none"
                                        bg="transparent"
                                        borderColor="transparent"
                                        color={
                                          hasError ? "red.500" : "gray.500"
                                        }
                                        _hover={{
                                          bg: hasError ? "red.50" : "brand.50",
                                          color: hasError
                                            ? "red.600"
                                            : "brand.600",
                                        }}
                                        transition="all 0.2s"
                                      >
                                        <Flex alignItems="center" gap={0.5}>
                                          <IoMdSettings />
                                          <IoCaretDownSharp
                                            style={{ fontSize: "8px" }}
                                          />
                                        </Flex>
                                      </IconButton>
                                    </Menu.Trigger>
                                    <Portal>
                                      <Menu.Positioner>
                                        <Menu.Content>
                                          <Menu.Item
                                            value="edit-settings"
                                            cursor="pointer"
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              setActiveTableForSettings(table);
                                              setSettingsModalOpen(true);
                                            }}
                                          >
                                            Edit
                                          </Menu.Item>
                                          <Menu.Item
                                            value="copy-settings"
                                            cursor="pointer"
                                            disabled={
                                              selectedTables.length <= 1 ||
                                              !isSelected
                                            }
                                            onClick={(e) => {
                                              e.stopPropagation();
                                              setActiveTableForCopy(table);
                                              setCopyType("export");
                                              setSelectedCopyTargets([]);
                                            }}
                                          >
                                            Copy
                                          </Menu.Item>
                                        </Menu.Content>
                                      </Menu.Positioner>
                                    </Portal>
                                  </Menu.Root>
                                </Tooltip>
                              </Flex>
                            );
                          })()}

                          {isEmailSupportedDestination &&
                            (() => {
                              const isEmailConfigured = !!(
                                (tableExportSettings?.[table]
                                  ?.notification_email_group_ids?.length ?? 0) >
                                  0 ||
                                (item.notification_email_group_ids?.length ??
                                  0) > 0 ||
                                tableExportSettings?.[table]
                                  ?.email_custom_fields?.subject ||
                                tableExportSettings?.[table]
                                  ?.email_custom_fields?.body_content ||
                                (tableExportSettings?.[table]
                                  ?.email_custom_fields?.body_fields?.length ??
                                  0) > 0 ||
                                item.email_custom_fields?.subject ||
                                item.email_custom_fields?.body_content ||
                                (item.email_custom_fields?.body_fields
                                  ?.length ?? 0) > 0
                              );
                              return (
                                <Flex
                                  justifyContent="center"
                                  w="50px"
                                  flexShrink={0}
                                >
                                  <Tooltip content="Email Notifications">
                                    <Menu.Root
                                      positioning={{ placement: "bottom-end" }}
                                    >
                                      <Menu.Trigger asChild>
                                        <IconButton
                                          size="xs"
                                          variant="ghost"
                                          colorPalette="brand"
                                          disabled={
                                            !!activeTableForCopy || !isSelected
                                          }
                                          onClick={(e) => e.stopPropagation()}
                                          borderRadius="md"
                                          height="22px"
                                          px={1.5}
                                          minWidth="34px"
                                          flexShrink={0}
                                          boxShadow="none"
                                          bg="transparent"
                                          color="gray.500"
                                          borderColor="transparent"
                                          _hover={{
                                            bg: "brand.50",
                                            color: "brand.600",
                                          }}
                                          transition="all 0.2s"
                                        >
                                          <Flex alignItems="center" gap={0.5}>
                                            <IoMdMail />
                                            <IoCaretDownSharp
                                              style={{ fontSize: "8px" }}
                                            />
                                          </Flex>
                                        </IconButton>
                                      </Menu.Trigger>
                                      <Portal>
                                        <Menu.Positioner>
                                          <Menu.Content>
                                            <Menu.Item
                                              value="edit-email"
                                              cursor="pointer"
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                setActiveTableForEmail(table);
                                                setEmailModalOpen(true);
                                              }}
                                            >
                                              Edit
                                            </Menu.Item>
                                            <Menu.Item
                                              value="copy-email"
                                              cursor="pointer"
                                              disabled={
                                                selectedTables.length <= 1 ||
                                                !isSelected ||
                                                !isEmailConfigured
                                              }
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                setActiveTableForCopy(table);
                                                setCopyType("email");
                                                setSelectedCopyTargets([]);
                                              }}
                                            >
                                              Copy
                                            </Menu.Item>
                                          </Menu.Content>
                                        </Menu.Positioner>
                                      </Portal>
                                    </Menu.Root>
                                  </Tooltip>
                                </Flex>
                              );
                            })()}

                          <Flex justifyContent="center" w="48px" flexShrink={0}>
                            <Tooltip
                              content={
                                batchName
                                  ? `Assigned to batch "${batchName}" — remove from batch to deselect`
                                  : undefined
                              }
                              disabled={!batchName}
                            >
                              <Box>
                                <Checkbox.Root
                                  colorPalette="brand"
                                  variant="solid"
                                  checked={isSelected || !!batchName}
                                  onCheckedChange={() =>
                                    toggleTableSelection(table)
                                  }
                                  disabled={!!activeTableForCopy || !!batchName}
                                >
                                  <Checkbox.HiddenInput />
                                  <Checkbox.Control
                                    cursor={
                                      !!activeTableForCopy || !!batchName
                                        ? "not-allowed"
                                        : "pointer"
                                    }
                                  />
                                </Checkbox.Root>
                              </Box>
                            </Tooltip>
                          </Flex>
                        </Flex>
                      </Grid>

                      {isExpanded && (
                        <Flex
                          direction="column"
                          gap={2}
                          paddingBlock={4}
                          width="100%"
                        >
                          {table_fields &&
                            Object.entries(table_fields)
                              .sort(([nameA, infoA], [nameB, infoB]) => {
                                const isPKA = isPrimaryKey(nameA, infoA);
                                const isPKB = isPrimaryKey(nameB, infoB);
                                if (isPKA && !isPKB) return -1;
                                if (!isPKA && isPKB) return 1;
                                return 0;
                              })
                              .map(([field, fieldInfo]) => {
                                const dataType =
                                  typeof fieldInfo === "string"
                                    ? fieldInfo
                                    : (fieldInfo as { data_type: string })
                                        .data_type;
                                const isPK = isPrimaryKey(field, fieldInfo);

                                return (
                                  <Flex
                                    key={field}
                                    direction="column"
                                    gap={1}
                                    width="100%"
                                  >
                                    <Flex alignItems="center" gap={2}>
                                      {isPK && (
                                        <Box color="yellow.500">
                                          <PiKeyFill />
                                        </Box>
                                      )}
                                      <Text fontSize="sm">
                                        {field}: {dataType}
                                      </Text>
                                    </Flex>
                                  </Flex>
                                );
                              })}
                        </Flex>
                      )}
                    </Flex>
                  );
                })}
              </Flex>

              {totalPages > 1 && (
                <Box
                  flexShrink={0}
                  mt={2}
                  opacity={activeTableForCopy ? 0.5 : 1}
                  pointerEvents={activeTableForCopy ? "none" : "auto"}
                >
                  <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    onPageChange={jumpToPage}
                  />
                </Box>
              )}
            </>
          )}
        </Flex>

        <BatchGroupedPanel
          connectionId={connector.connection_id}
          pendingUnassignedTables={pendingUnassignedTables}
        />
      </Grid>

      {/* Copy mode action bar */}
      <ActionBar.Root open={!!activeTableForCopy}>
        <Portal>
          <ActionBar.Positioner>
            <ActionBar.Content>
              <Checkbox.Root
                colorPalette="brand"
                checked={
                  selectedCopyTargets.length > 0 &&
                  selectedCopyTargets.length ===
                    selectedSourceTables.filter(
                      (t) => t.table !== activeTableForCopy,
                    ).length
                    ? true
                    : selectedCopyTargets.length > 0 &&
                        selectedCopyTargets.length <
                          selectedSourceTables.filter(
                            (t) => t.table !== activeTableForCopy,
                          ).length
                      ? "indeterminate"
                      : false
                }
                onCheckedChange={(details) => {
                  if (details.checked === true) {
                    setSelectedCopyTargets(
                      selectedSourceTables
                        .map((t) => t.table)
                        .filter((t) => t !== activeTableForCopy),
                    );
                  } else {
                    setSelectedCopyTargets([]);
                  }
                }}
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Text fontSize="xs" color="gray.600" fontWeight="semibold">
                  Select All
                </Text>
              </Checkbox.Root>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setActiveTableForCopy(null);
                  setCopyType(null);
                  setSelectedCopyTargets([]);
                }}
                disabled={isSavingSelection}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                colorPalette="brand"
                onClick={() => handleCopySettings(selectedCopyTargets)}
                loading={isSavingSelection}
                disabled={selectedCopyTargets.length === 0}
              >
                Paste
              </Button>
            </ActionBar.Content>
          </ActionBar.Positioner>
        </Portal>
      </ActionBar.Root>

      {/* Selection save action bar */}
      <ActionBar.Root open={isSelectionDirty}>
        <Portal>
          <ActionBar.Positioner>
            <ActionBar.Content>
              <Button
                variant="solid"
                colorPalette="brand"
                size="sm"
                onClick={() => handleSaveSelection()}
                loading={isSavingSelection}
              >
                Save
              </Button>
            </ActionBar.Content>
          </ActionBar.Positioner>
        </Portal>
      </ActionBar.Root>
      <FieldSelectionModal
        open={fieldModalOpen}
        onClose={() => setFieldModalOpen(false)}
        tableName={activeTableForFields || ""}
        tableFields={activeTableData?.table_fields || {}}
        initialSelectedFields={activeTableData?.selected_fields}
        onSave={handleSaveFields}
        isSaving={isUpdatingFields}
      />

      {transformModalOpen && activeTransformTableItem && (
        <FileExportTransformModal
          open={transformModalOpen}
          onClose={() => {
            setTransformModalOpen(false);
            setActiveTableForTransform(null);
          }}
          connectionId={connector.connection_id}
          tableItem={activeTransformTableItem}
        />
      )}

      <EmailGroupSelectionModal
        open={emailModalOpen}
        onClose={() => setEmailModalOpen(false)}
        tableName={activeTableForEmail || ""}
        emailGroups={emailGroups}
        initialSelectedGroupIds={
          (activeTableForEmail &&
            tableExportSettings[activeTableForEmail]
              ?.notification_email_group_ids) ||
          []
        }
        initialEmailTemplateId={
          activeTableForEmail
            ? tableExportSettings[activeTableForEmail]?.email_template_id
            : undefined
        }
        initialEmailCustomFields={
          activeTableForEmail
            ? tableExportSettings[activeTableForEmail]?.email_custom_fields
            : undefined
        }
        destinationName={connector.destination_name || ""}
        pathLabel={exportConfig?.destination?.path_label}
        onSave={handleSaveEmailGroups}
        isSaving={isUpdatingEmails}
        rootFolder={connector.root_folder}
        targetFolder={
          activeTableForEmail
            ? tableExportSettings[activeTableForEmail]?.target_folder
            : undefined
        }
        outputFileName={
          activeTableForEmail
            ? tableExportSettings[activeTableForEmail]?.output_file_name
            : undefined
        }
        fileFormat={
          activeTableForEmail
            ? tableExportSettings[activeTableForEmail]?.file_format
            : undefined
        }
        connectionName={connector.source_title || connector.display_name || ""}
        companyName={connector.company_name}
        sourceDisplayName={connector.display_name || connector.source_name}
        columnsCount={emailTableColumnsCount}
      />

      {activeTableForSettings && activeTableSettings && (
        <TableExportSettingsModal
          open={settingsModalOpen}
          onClose={() => {
            setSettingsModalOpen(false);
            setActiveTableForSettings(null);
          }}
          tableName={activeTableForSettings}
          settings={activeTableSettings}
          tableFields={activeTableFields}
          selectedFields={activeTableSelectedFields}
          isSaving={isUpdatingSettings}
          supportedFormats={exportConfig?.destination?.supported_formats}
          onSave={(localSettings) => {
            const currentTableSetting =
              tableExportSettings[activeTableForSettings] ||
              activeTableSettings;
            const normalizedSettings = sanitizeTableExportSetting(
              {
                ...currentTableSetting,
                ...localSettings,
                output_file_name:
                  localSettings.output_file_name.trim() ||
                  activeTableForSettings,
              },
              isEmailSupportedDestination,
              activeTableForSettings,
            );

            updateTableExportSettings(
              {
                tableName: activeTableForSettings,
                settings: normalizedSettings,
              },
              {
                onSuccess: () => {
                  toaster.success({
                    title: "Settings saved successfully",
                  });
                  setTableExportSettings((prev) => ({
                    ...prev,
                    [activeTableForSettings]: normalizedSettings,
                  }));
                  setTargetFolderErrors((prev) => {
                    const next = { ...prev };
                    delete next[activeTableForSettings];
                    return next;
                  });
                  setSettingsModalOpen(false);
                  setActiveTableForSettings(null);
                },
              },
            );
          }}
        />
      )}
    </Flex>
  );
};

export default SnowflakeFileExportSchema;
