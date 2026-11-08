import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  IconButton,
  Input,
  Link,
  Portal,
  Switch,
  Text,
  VStack,
} from "@chakra-ui/react";

import {
  FiMaximize2,
  FiMinimize2,
  FiRotateCcw,
  FiRotateCw,
} from "react-icons/fi";

import { isDatabricksConnector } from "@/components/dashboard/helpers/helpers";
import {
  ConfirmHost,
  confirm,
  useConfirmPending,
} from "@/components/ui/confirm";
import { toaster } from "@/components/ui/toaster";
import {
  DEFAULT_DRY_RUN_ROWS,
  MAX_MAPPING_JSON_BYTES,
  MAX_SCRIPT_BYTES,
  MAX_SCRIPT_LINES,
} from "@/constants/transform-limits";
import usePermissions from "@/hooks/usePermissions";
import {
  useDeactivateTransform,
  useDryRunTransform,
  useEntityTransform,
  useSaveDraftTransform,
  useValidateTransform,
} from "@/queryOptions/connector/schema/transform/useEntityTransform";
import {
  type ConnectorTable,
  type MappingTableRow,
  type TransformDryRunResponse,
  type TransformValidateResponse,
} from "@/types/connectors";
import {
  type DestinationNameSource,
  defaultDestinationDisplayName,
  defaultDestinationTableName,
  extractJsonTargetObjectname,
  isValidSnowflakeDestinationName,
  normalizeDestinationTableName,
  resolveDestinationTableName,
} from "@/utils/snowflakeDestinationTableName";

import ColumnMapper from "./ColumnMapper";
import DestinationTableField from "./DestinationTableField";
import ExpandableCodeEditor, {
  type EditorDiagnostic,
  type EditorValidation,
} from "./ExpandableCodeEditor";
import ExpandablePreviewTable from "./ExpandablePreviewTable";
import JsonFileChip from "./JsonFileChip";
import ScriptFileChip from "./ScriptFileChip";
import VirtualMappingTable from "./VirtualMappingTable";
import { confirmCopy } from "./confirmCopy";
import {
  type HistoryState,
  type MappingBaseline,
  type MappingRow,
  type OverrideSnapshot,
  type SapColumn,
  addUserTarget,
  clearUserMapping,
  columnsRemainMapped,
  createHistory,
  emptyDraft,
  formatFieldType,
  isMappingDirty,
  jsonFileFromRaw,
  jsonSourceNames,
  keepApplicableOverrides,
  keyRemovalWarning,
  mappingCounters,
  objectNamesMatch,
  pushHistory,
  redoHistory,
  renameTarget,
  reorderMappedColumns,
  repointMapping,
  resetToJson,
  resetToJsonImpact,
  restoreAllJsonMappings,
  restoreJsonMapping,
  rowFilterSummary,
  selectEffectiveMappings,
  selectRemovedMappings,
  setJsonSourcesIncluded,
  snapshotOf,
  targetNameError,
  toggleAddedSource,
  undoHistory,
  validateMappingJsonStructure,
} from "./mappingState";
import { buildScriptExamples } from "./scriptExamples";
import {
  TRANSFORM_ENTRY_POINT,
  defaultScriptFileName,
  deriveRunOrder,
  inputSchemaFields,
  normalizeTransformScript,
  outputSchemaFields,
  persistedScriptText,
  scriptErrorLine,
  unwrapTransformBody,
  validateTransformRoot,
  wrapTransformBody,
} from "./scriptScaffold";

type FilterChip =
  | "all"
  | "unmapped_source"
  | "unmapped_target"
  | "warnings"
  | "removed";

interface TransformModalProps {
  open: boolean;
  onClose: () => void;
  connectionId: number;
  tableItem: ConnectorTable;
  modalTitle: string;
  destinationName?: string | null;
  occupiedDestinations?: Map<string, string>;
}

const STEPS = ["Column mapping", "Script", "Review and test"];
const pendingDraftRestores = new Set<string>();

const emptyOverrides = (): OverrideSnapshot => snapshotOf(emptyDraft());

const overridePayload = (snapshot: OverrideSnapshot) => ({
  addedSources: snapshot.addedSources,
  removedTargets: snapshot.removedTargets,
  removedJsonMappings: snapshot.removedJsonMappings || [],
  mappings: snapshot.overrideMappings,
  targetOrder: snapshot.targetOrder || [],
});

const hasManualOverrides = (snapshot: OverrideSnapshot) =>
  snapshot.overrideMappings.length > 0 ||
  snapshot.addedSources.length > 0 ||
  snapshot.removedTargets.length > 0 ||
  (snapshot.removedJsonMappings || []).length > 0 ||
  (snapshot.targetOrder || []).length > 0;

const snapshotFromSaved = (raw: unknown): OverrideSnapshot => {
  if (!raw || typeof raw !== "object") return emptyOverrides();
  const value = raw as Record<string, unknown>;
  const mappings = Array.isArray(value.mappings)
    ? value.mappings
    : Array.isArray(value.overrideMappings)
      ? value.overrideMappings
      : [];
  return {
    addedSources: Array.isArray(value.addedSources)
      ? value.addedSources.map(String)
      : [],
    removedTargets: Array.isArray(value.removedTargets)
      ? value.removedTargets.map(String)
      : [],
    removedJsonMappings: Array.isArray(value.removedJsonMappings)
      ? value.removedJsonMappings.map(String)
      : [],
    overrideMappings: mappings as OverrideSnapshot["overrideMappings"],
    targetOrder: Array.isArray(value.targetOrder)
      ? value.targetOrder.map(String)
      : [],
  };
};

const flagIsKey = (value: unknown) => {
  if (value === true || value === 1) return true;
  if (typeof value === "string") {
    const text = value.trim().toUpperCase();
    return text === "X" || text === "Y" || text === "TRUE" || text === "1";
  }
  return false;
};

const sapColumnsFromTable = (tableItem: ConnectorTable): SapColumn[] => {
  const fields = tableItem.table_fields || {};
  return Object.entries(fields).map(([name, info]) => {
    const meta = typeof info === "string" ? { data_type: info } : info || {};
    return {
      name,
      type: String(meta.data_type || meta.edm_type || ""),
      length: Number(meta.max_length || 0) || undefined,
      decimals: Number(meta.precision || 0) || undefined,
      isKey:
        flagIsKey(meta.isKey) ||
        flagIsKey(meta.is_key) ||
        flagIsKey(meta.key) ||
        flagIsKey(meta.key_flag),
    };
  });
};

const jsonFieldTypes = (
  raw: Record<string, unknown> | null,
  side: "source" | "target",
) => {
  const node = raw?.[side];
  const fields =
    node &&
    typeof node === "object" &&
    Array.isArray((node as { fields?: unknown }).fields)
      ? (node as { fields: unknown[] }).fields
      : [];
  const byName = new Map<string, string>();
  fields.forEach((field) => {
    if (!field || typeof field !== "object") return;
    const meta = field as {
      fieldname?: string;
      datatype?: string;
      length?: number;
      decimals?: number;
    };
    if (!meta.fieldname) return;
    byName.set(
      String(meta.fieldname),
      formatFieldType(
        meta.datatype,
        Number(meta.length || 0) || undefined,
        Number(meta.decimals || 0) || undefined,
      ),
    );
  });
  return byName;
};

const toTableRows = (
  rows: MappingRow[],
  sapColumns: SapColumn[],
  jsonRaw: Record<string, unknown> | null,
): MappingTableRow[] => {
  const sapByName = new Map(sapColumns.map((column) => [column.name, column]));
  const targetTypes = jsonFieldTypes(jsonRaw, "target");
  return rows.map((row) => {
    const source = row.source ? sapByName.get(row.source) : undefined;
    const typeLabel =
      targetTypes.get(row.target) ||
      formatFieldType(source?.type, source?.length, source?.decimals);
    return {
      sap_column: row.source,
      sap_columns: row.source ? [row.source] : [],
      target_column: row.target,
      renamed: Boolean(row.source && row.source !== row.target),
      status: row.status,
      added_by_user: row.origin === "USER",
      type_label: typeLabel,
    };
  });
};

const entityParts = (tableItem: ConnectorTable) => {
  const serviceName =
    tableItem.service_name || tableItem.table.split("/")[0] || "";
  const entityName = tableItem.service_name
    ? tableItem.table
    : tableItem.table.split("/")[1] || tableItem.table.split("/")[0] || "";
  return { serviceName, entityName, tableName: tableItem.table };
};

const TransformModal = ({
  open,
  onClose,
  connectionId,
  tableItem,
  modalTitle,
  destinationName,
  occupiedDestinations,
}: TransformModalProps) => {
  const parts = entityParts(tableItem);
  const defaultDisplay = defaultDestinationDisplayName(
    parts.serviceName,
    parts.entityName,
  );
  const defaultTable = defaultDestinationTableName(
    parts.serviceName,
    parts.entityName,
    parts.tableName,
  );

  const [step, setStep] = useState(0);
  const [mappingJson, setMappingJson] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [scriptText, setScriptText] = useState("");
  const [applyScript, setApplyScript] = useState(false);
  const [entryPoint, setEntryPoint] = useState(TRANSFORM_ENTRY_POINT);
  const [scriptNameFromUpload, setScriptNameFromUpload] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterChip>("all");
  const [parsing, setParsing] = useState(false);
  const [validateResult, setValidateResult] =
    useState<TransformValidateResponse | null>(null);
  /** Script text the latest validation checklist was computed for. */
  const [markedScriptText, setMarkedScriptText] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] =
    useState<TransformDryRunResponse | null>(null);

  const [destSource, setDestSource] =
    useState<DestinationNameSource>("DEFAULT");
  const [destName, setDestName] = useState(defaultTable);
  const [jsonRawName, setJsonRawName] = useState<string | null>(null);
  const [isEditingDest, setIsEditingDest] = useState(false);
  const [editDestValue, setEditDestValue] = useState("");
  const [destError, setDestError] = useState<string | null>(null);
  const [destWarnings, setDestWarnings] = useState<string[]>([]);
  const [confirmUseExisting, setConfirmUseExisting] = useState(false);
  const [renameMode, setRenameMode] = useState<
    "create_new" | "rename_existing" | null
  >(null);
  const [columnAddMode, setColumnAddMode] = useState<
    "full_reload" | "new_columns_only" | null
  >(null);
  const [history, setHistory] = useState<HistoryState>(() =>
    createHistory(emptyOverrides()),
  );
  const [jsonFileMeta, setJsonFileMeta] = useState<{
    fileName: string;
    size: number;
  } | null>(null);
  const [scriptFileMeta, setScriptFileMeta] = useState<{
    fileName: string;
    size: number;
  } | null>(null);
  /** Last script text that passed Save and validate (or loaded from the server). */
  const [scriptValidatedText, setScriptValidatedText] = useState("");
  const [jsonErrors, setJsonErrors] = useState<
    { path: string; message: string }[]
  >([]);
  const [viewMode, setViewMode] = useState<"mapper" | "table">("mapper");
  const [modalExpanded, setModalExpanded] = useState(false);
  const [baseline, setBaseline] = useState<MappingBaseline | null>(null);
  const [announce, setAnnounce] = useState("");
  const historyRef = useRef(history);
  historyRef.current = history;
  const scriptTextRef = useRef(scriptText);
  scriptTextRef.current = scriptText;
  const openerRef = useRef<HTMLElement | null>(null);
  const closeGuard = useRef(false);
  const mountedRef = useRef(true);
  const columnAddModeRef = useRef(columnAddMode);
  columnAddModeRef.current = columnAddMode;
  const columnAddRequest = useRef<Promise<boolean> | null>(null);
  const { can } = usePermissions();
  const readOnly = !can("can_edit_tables");
  const confirmOpen = useConfirmPending();
  const effectiveRunOrder = useMemo(
    () => deriveRunOrder(mappingJson),
    [mappingJson],
  );

  const destNameRef = useRef(destName);
  destNameRef.current = destName;
  const destSourceRef = useRef(destSource);
  destSourceRef.current = destSource;

  const { data: transformData } = useEntityTransform(
    connectionId,
    tableItem.table,
  );
  const validateMut = useValidateTransform(connectionId, tableItem.table);
  const dryRunMut = useDryRunTransform(connectionId, tableItem.table);
  const draftSaveMut = useSaveDraftTransform(connectionId, tableItem.table);
  const deactivateMut = useDeactivateTransform(connectionId, tableItem.table);
  const [draftSaveLabel, setDraftSaveLabel] = useState<string | null>(null);
  const skipAutosaveUntilRef = useRef(0);

  const previousSavedDest = (
    transformData?.active?.destination_table_name || ""
  )
    .trim()
    .toUpperCase();
  const hasLoadedData = Boolean(
    tableItem.initial_completed_flag || tableItem.first_sync_timestamp,
  );
  const publishedVersion = transformData?.active?.version ?? null;

  const draftKey = `transform-draft:${connectionId}:${tableItem.table}`;
  const loadedKeyRef = useRef("");

  useEffect(() => {
    if (!open) {
      loadedKeyRef.current = "";
      return;
    }
    const loadKey = `${tableItem.table}:a${transformData?.active?.version ?? "none"}:d${transformData?.draft?.version ?? "none"}`;
    if (loadedKeyRef.current === loadKey) return;
    loadedKeyRef.current = loadKey;
    // Prefer open server draft so autosaved edits reload; else published active.
    const sourceRow = transformData?.draft ?? transformData?.active ?? null;
    const normalizedScript = normalizeTransformScript(
      sourceRow?.script_text || "",
    );
    if (sourceRow) {
      setMappingJson(
        (sourceRow.mapping_json as Record<string, unknown>) || null,
      );
      setScriptText(normalizedScript);
      setApplyScript(Boolean(unwrapTransformBody(normalizedScript).trim()));
      setEntryPoint(TRANSFORM_ENTRY_POINT);
      setScriptNameFromUpload(false);
      const source =
        (sourceRow.destination_name_source as DestinationNameSource) ||
        "DEFAULT";
      setDestSource(source);
      setDestName(
        (sourceRow.destination_table_name || defaultTable).toUpperCase(),
      );
      setJsonRawName(sourceRow.json_table_name || null);
      setHistory(createHistory(snapshotFromSaved(sourceRow.mapping_overrides)));
      setJsonFileMeta(
        sourceRow.mapping_json
          ? {
              fileName:
                sourceRow.status === "draft"
                  ? "Draft mapping"
                  : "Saved mapping",
              size: 0,
            }
          : null,
      );
      setScriptFileMeta(
        normalizedScript.trim()
          ? {
              fileName: defaultScriptFileName(
                (sourceRow.mapping_json as Record<string, unknown>) || null,
                parts.entityName,
              ),
              size: new Blob([normalizedScript]).size,
            }
          : null,
      );
    } else {
      setMappingJson(null);
      setScriptText("");
      setApplyScript(false);
      setEntryPoint(TRANSFORM_ENTRY_POINT);
      setScriptNameFromUpload(false);
      setDestSource("DEFAULT");
      setDestName(defaultTable);
      setJsonRawName(null);
      setHistory(createHistory(emptyOverrides()));
      setJsonFileMeta(null);
      setScriptFileMeta(null);
    }
    const loadedBaseline: MappingBaseline = sourceRow
      ? {
          mappingJson:
            (sourceRow.mapping_json as Record<string, unknown>) || null,
          overrides: snapshotFromSaved(sourceRow.mapping_overrides),
          scriptText: normalizedScript,
          entryPoint: TRANSFORM_ENTRY_POINT,
          runOrder: deriveRunOrder(
            (sourceRow.mapping_json as Record<string, unknown>) || null,
          ),
          destName: (
            sourceRow.destination_table_name || defaultTable
          ).toUpperCase(),
          destSource:
            (sourceRow.destination_name_source as DestinationNameSource) ||
            "DEFAULT",
        }
      : {
          mappingJson: null,
          overrides: emptyOverrides(),
          scriptText: "",
          entryPoint: TRANSFORM_ENTRY_POINT,
          runOrder: deriveRunOrder(null),
          destName: defaultTable,
          destSource: "DEFAULT",
        };
    setBaseline(loadedBaseline);
    setScriptValidatedText(loadedBaseline.scriptText);
    skipAutosaveUntilRef.current = Date.now() + 1500;
    setJsonErrors([]);
    setModalExpanded(false);
    setStep(0);
    setValidateResult(null);
    setMarkedScriptText(null);
    setDryRunResult(null);
    setIsEditingDest(false);
    setDestError(null);
    setDestWarnings([]);
    setConfirmUseExisting(false);
    setRenameMode(null);
    setColumnAddMode(null);
    const stored = sessionStorage.getItem(draftKey);
    if (!stored) return;
    let parsed: {
      mappingJson?: Record<string, unknown> | null;
      overrides?: OverrideSnapshot;
      scriptText?: string;
      destName?: string;
      destSource?: DestinationNameSource;
      fileName?: string;
      fileSize?: number;
      scriptFileName?: string;
      scriptFileSize?: number;
      step?: number;
      applyScript?: boolean;
      dryRunResult?: TransformDryRunResponse | null;
      scriptValidatedText?: string;
      scriptNameFromUpload?: boolean;
    };
    try {
      parsed = JSON.parse(stored) as typeof parsed;
    } catch {
      sessionStorage.removeItem(draftKey);
      return;
    }
    const token = loadKey;
    if (pendingDraftRestores.has(token)) return;
    pendingDraftRestores.add(token);
    void (async () => {
      try {
        const restore = await confirm({
          title: confirmCopy.restoreDraft.title,
          description: confirmCopy.restoreDraft.description,
          cancelLabel: confirmCopy.restoreDraft.cancelLabel,
          confirmLabel: confirmCopy.restoreDraft.confirmLabel,
          icon: "info",
        });
        if (loadedKeyRef.current !== token) return;
        if (!restore) {
          sessionStorage.removeItem(draftKey);
          return;
        }
        if (parsed.mappingJson) setMappingJson(parsed.mappingJson);
        if (parsed.overrides) setHistory(createHistory(parsed.overrides));
        if (typeof parsed.scriptText === "string") {
          const restored = validateTransformRoot(parsed.scriptText)
            ? normalizeTransformScript(parsed.scriptText)
            : parsed.scriptText;
          setScriptText(restored);
          setEntryPoint(TRANSFORM_ENTRY_POINT);
          if (typeof parsed.applyScript === "boolean") {
            setApplyScript(parsed.applyScript);
          } else {
            setApplyScript(Boolean(unwrapTransformBody(restored).trim()));
          }
        } else if (typeof parsed.applyScript === "boolean") {
          setApplyScript(parsed.applyScript);
        }
        if (typeof parsed.step === "number") {
          setStep(Math.min(2, Math.max(0, parsed.step)));
        }
        if (parsed.dryRunResult && typeof parsed.dryRunResult === "object") {
          setDryRunResult(parsed.dryRunResult);
        }
        if (typeof parsed.scriptValidatedText === "string") {
          setScriptValidatedText(parsed.scriptValidatedText);
        }
        setScriptNameFromUpload(Boolean(parsed.scriptNameFromUpload));
        if (parsed.destName) setDestName(parsed.destName);
        if (parsed.destSource) setDestSource(parsed.destSource);
        if (parsed.fileName) {
          setJsonFileMeta({
            fileName: parsed.fileName,
            size: parsed.fileSize || 0,
          });
        }
        if (parsed.scriptFileName) {
          setScriptFileMeta({
            fileName: parsed.scriptFileName,
            size: parsed.scriptFileSize || 0,
          });
        }
      } finally {
        pendingDraftRestores.delete(token);
      }
    })();
  }, [
    open,
    transformData?.active,
    transformData?.draft,
    defaultTable,
    draftKey,
    tableItem.table,
    parts.entityName,
  ]);

  const sapColumns = useMemo(() => {
    const fromTable = sapColumnsFromTable(tableItem);
    if (fromTable.length > 0) return fromTable;
    const source = mappingJson?.source;
    if (!source || typeof source !== "object") return fromTable;
    const fields = (source as { fields?: unknown }).fields;
    if (!Array.isArray(fields)) return fromTable;
    return fields
      .map((field) =>
        field && typeof field === "object"
          ? (field as {
              fieldname?: string;
              datatype?: string;
              length?: number;
            })
          : null,
      )
      .filter(
        (
          field,
        ): field is { fieldname: string; datatype?: string; length?: number } =>
          Boolean(field?.fieldname),
      )
      .map((field) => ({
        name: field.fieldname,
        type: String(field.datatype || ""),
        length: Number(field.length || 0) || undefined,
      }));
  }, [mappingJson, tableItem]);
  const draft = useMemo(
    () => ({
      json: mappingJson
        ? jsonFileFromRaw(
            mappingJson,
            jsonFileMeta?.fileName || "Saved mapping",
            jsonFileMeta?.size || 0,
          )
        : null,
      sapColumns,
      ...history.present,
    }),
    [history.present, jsonFileMeta, mappingJson, sapColumns],
  );
  const currentMappingDraft = () => ({
    json: mappingJson
      ? jsonFileFromRaw(
          mappingJson,
          jsonFileMeta?.fileName || "Saved mapping",
          jsonFileMeta?.size || 0,
        )
      : null,
    sapColumns,
    ...historyRef.current.present,
  });
  const effectiveRows = useMemo(() => selectEffectiveMappings(draft), [draft]);
  const removedRows = useMemo(() => selectRemovedMappings(draft), [draft]);
  const counts = useMemo(
    () => mappingCounters(effectiveRows, sapColumns, removedRows),
    [effectiveRows, removedRows, sapColumns],
  );
  const mappingTable = useMemo(
    () => toTableRows(effectiveRows, sapColumns, mappingJson),
    [effectiveRows, mappingJson, sapColumns],
  );
  const scriptSchemaFolds = useMemo(() => {
    const rows = effectiveRows.map((row) => ({
      source: row.source,
      target: row.target,
    }));
    const sapForSchema = sapColumns.map((column) => ({
      name: column.name,
      type: column.type,
      length: column.length,
      decimals:
        "decimals" in column && column.decimals !== undefined
          ? column.decimals
          : undefined,
    }));
    return [
      {
        name: "__source_df",
        fields: inputSchemaFields(rows, mappingJson, sapForSchema),
      },
      {
        name: "__target_df",
        fields: outputSchemaFields(rows, mappingJson, sapForSchema),
      },
    ];
  }, [effectiveRows, mappingJson, sapColumns]);
  const scriptExamples = useMemo(
    () =>
      buildScriptExamples(
        scriptSchemaFolds[0]?.fields.map((field) => field.name) ?? [],
        scriptSchemaFolds[1]?.fields.map((field) => field.name) ?? [],
      ),
    [scriptSchemaFolds],
  );
  const filterSummary = rowFilterSummary(mappingJson);
  const entityMismatch =
    Boolean(mappingJson) &&
    !objectNamesMatch(parts.entityName, draft.json?.sourceObject || null);

  const commitOverrides = useCallback((next: OverrideSnapshot) => {
    setHistory((current) => pushHistory(current, next));
  }, []);

  const notifyRemoval = (row: MappingRow | undefined) => {
    const keyMessage = keyRemovalWarning(row, sapColumns);
    const scriptNote =
      row && !row.source && row.status === "From script"
        ? "The script may still produce this column. The schema check on Review will flag extra columns."
        : undefined;
    const title =
      keyMessage || confirmCopy.removed(row?.source || row?.target || "Column");
    const payload = {
      title,
      description: scriptNote,
      action: {
        label: "Undo",
        onClick: () => {
          setHistory((current) => undoHistory(current));
        },
      },
    };
    if (keyMessage || scriptNote) toaster.warning(payload);
    else toaster.success(payload);
  };

  const collisionLabel = useMemo(() => {
    const name = destName.trim().toUpperCase();
    if (!name) return null;
    const occupied = occupiedDestinations?.get(name);
    if (occupied) return occupied;
    return validateResult?.destination?.collision?.entity_label || null;
  }, [destName, occupiedDestinations, validateResult?.destination?.collision]);

  const applyResolved = useCallback(
    (
      mapping: Record<string, unknown> | null,
      source: DestinationNameSource,
      manual?: string | null,
    ) => {
      const resolved = resolveDestinationTableName({
        serviceName: parts.serviceName,
        entityName: parts.entityName,
        tableName: parts.tableName,
        mappingJson: mapping,
        destinationNameSource: source,
        manualDestinationTableName: manual,
      });
      setDestSource(resolved.destinationNameSource);
      setDestName(resolved.destinationTableName);
      setJsonRawName(resolved.jsonTableName);
      setDestWarnings(resolved.warnings);
      if (!resolved.valid || !resolved.destinationTableName) {
        setDestError("Destination table name is invalid.");
      } else {
        setDestError(null);
      }
      return resolved;
    },
    [parts.entityName, parts.serviceName, parts.tableName],
  );

  const applyMappingJson = useCallback(
    async (
      data: Record<string, unknown>,
      file?: { name: string; size: number },
    ) => {
      const structureErrors = validateMappingJsonStructure(data);
      setJsonErrors(structureErrors);
      if (structureErrors.length > 0) {
        toaster.error({
          title: "Mapping JSON has errors",
          description: structureErrors[0]
            ? `${structureErrors[0].path}: ${structureErrors[0].message}`
            : undefined,
        });
      }
      const currentOverrides = historyRef.current.present;
      let nextOverrides = emptyOverrides();
      if (hasManualOverrides(currentOverrides)) {
        const choice = await confirm({
          title: confirmCopy.replaceJson.title,
          description: confirmCopy.replaceJson.description,
          cancelLabel: confirmCopy.replaceJson.cancelLabel,
          confirmLabel: confirmCopy.replaceJson.confirmLabel,
          icon: "warning",
          alternate: {
            label: confirmCopy.replaceJson.alternateLabel,
            tone: "danger",
          },
        });
        if (choice === "cancel") return;
        if (choice === "confirm") {
          nextOverrides = keepApplicableOverrides(
            currentOverrides,
            data,
            sapColumns,
          );
        }
      }
      setHistory(createHistory(nextOverrides));
      setJsonFileMeta({
        fileName: file?.name || "Saved mapping",
        size: file?.size || 0,
      });

      const jsonRaw = extractJsonTargetObjectname(data);
      const normalised = jsonRaw
        ? normalizeDestinationTableName(jsonRaw)
        : { name: "", warnings: [] as string[] };
      const current = destNameRef.current;
      const source = destSourceRef.current;

      const commitLoadedMapping = (loaded: Record<string, unknown>) => {
        setMappingJson(loaded);
        if (scriptTextRef.current.trim()) return;
        const seeded = wrapTransformBody("");
        scriptTextRef.current = seeded;
        setScriptText(seeded);
        setEntryPoint(TRANSFORM_ENTRY_POINT);
        setBaseline((current) =>
          current && !current.scriptText.trim()
            ? {
                ...current,
                scriptText: seeded,
                entryPoint: TRANSFORM_ENTRY_POINT,
              }
            : current,
        );
      };

      if (source === "MANUAL") {
        commitLoadedMapping(data);
        setJsonRawName(jsonRaw);
        toaster.success({ title: "Mapping JSON loaded" });
        return;
      }

      if (
        jsonRaw &&
        normalised.name &&
        current &&
        current !== normalised.name &&
        current !== defaultTable
      ) {
        const loadedNameChanged = Boolean(
          hasLoadedData &&
            previousSavedDest &&
            previousSavedDest !== normalised.name,
        );
        if (loadedNameChanged) {
          const mode = await confirm({
            title: confirmCopy.renameDestination.title,
            description: confirmCopy.renameDestination.description(
              previousSavedDest,
              normalised.name,
            ),
            cancelLabel: confirmCopy.renameDestination.cancelLabel,
            confirmLabel: confirmCopy.renameDestination.confirmLabel,
            icon: "warning",
            choices: confirmCopy.renameDestination.choices,
          });
          commitLoadedMapping(data);
          setJsonRawName(jsonRaw);
          if (mode !== "create_new" && mode !== "rename_existing") {
            setDestSource("MANUAL");
            toaster.success({ title: "Mapping JSON loaded" });
            return;
          }
          setRenameMode(mode);
          setDestSource("JSON");
          setDestName(normalised.name);
          setDestWarnings(normalised.warnings);
          setDestError(null);
          setConfirmUseExisting(false);
          toaster.success({ title: "Mapping JSON loaded" });
          return;
        }
        const rename = await confirm({
          title: confirmCopy.renameDestination.title,
          description: confirmCopy.renameDestination.plainDescription(
            current,
            normalised.name,
          ),
          cancelLabel: confirmCopy.renameDestination.cancelLabel,
          confirmLabel: confirmCopy.renameDestination.renameLabel,
          icon: "info",
        });
        if (!rename) {
          commitLoadedMapping(data);
          setDestSource("MANUAL");
          setJsonRawName(jsonRaw);
          toaster.success({ title: "Mapping JSON loaded" });
          return;
        }
      }

      commitLoadedMapping(data);
      if (jsonRaw && normalised.name) {
        setDestSource("JSON");
        setDestName(normalised.name);
        setJsonRawName(jsonRaw);
        setDestWarnings(normalised.warnings);
        setDestError(null);
      } else {
        applyResolved(data, "DEFAULT");
      }
      setConfirmUseExisting(false);
      setRenameMode(null);
      toaster.success({ title: "Mapping JSON loaded" });
    },
    [applyResolved, defaultTable, hasLoadedData, previousSavedDest, sapColumns],
  );

  const handleFileDrop = useCallback(
    async (file: File) => {
      if (file.size > MAX_MAPPING_JSON_BYTES) {
        toaster.error({
          title: `JSON file exceeds ${MAX_MAPPING_JSON_BYTES / (1024 * 1024)} MB limit`,
        });
        return;
      }
      setParsing(true);
      try {
        const text = await file.text();
        const worker = new Worker(
          new URL("@/workers/parseMappingJson.worker.ts", import.meta.url),
          { type: "module" },
        );
        worker.postMessage(text);
        worker.onmessage = (ev) => {
          const result = ev.data as {
            ok: boolean;
            data?: Record<string, unknown>;
            error?: string;
          };
          if (result.ok && result.data) {
            void applyMappingJson(result.data, {
              name: file.name,
              size: file.size,
            });
          } else {
            toaster.error({
              title: result.error || "Invalid JSON",
              action: {
                label: confirmCopy.retry,
                onClick: () => {
                  void handleFileDrop(file);
                },
              },
            });
          }
          setParsing(false);
          worker.terminate();
        };
      } catch {
        setParsing(false);
        toaster.error({
          title: "Failed to read file",
          action: {
            label: confirmCopy.retry,
            onClick: () => {
              void handleFileDrop(file);
            },
          },
        });
      }
    },
    [applyMappingJson],
  );

  const destPayload = useMemo(
    () => ({
      destination_name_source: destSource,
      destination_table_name: destName,
    }),
    [destName, destSource],
  );

  const runValidate = useCallback(async () => {
    try {
      const result = await validateMut.mutateAsync({
        mapping_json: mappingJson,
        mapping_overrides: overridePayload(history.present),
        script_text: persistedScriptText(applyScript, scriptText),
        entry_point: TRANSFORM_ENTRY_POINT,
        ...destPayload,
      });
      setValidateResult(result);
      setMarkedScriptText(persistedScriptText(applyScript, scriptText));
      if (result.destination?.warnings?.length) {
        setDestWarnings(result.destination.warnings);
      }
      if (result.destination?.error) {
        setDestError(result.destination.error);
      }
      return result;
    } catch {
      setValidateResult(null);
      setMarkedScriptText(null);
      toaster.error({
        title: "Mapping preview unavailable",
        description:
          "Validate failed. Check that FastAPI is deployed with entity-transform routes.",
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void runValidate();
          },
        },
      });
      throw new Error("validate failed");
    }
  }, [
    applyScript,
    validateMut,
    mappingJson,
    scriptText,
    destPayload,
    history.present,
  ]);

  const runScriptValidation = useCallback(async () => {
    try {
      const result = await runValidate();
      if (!persistedScriptText(applyScript, scriptText).trim()) {
        toaster.info({ title: "No script to validate" });
        return { ok: false as const, result };
      }
      const scriptIds = new Set([
        "parse",
        "imports",
        "banned",
        "size",
        "entry_point",
      ]);
      const scriptItems =
        result.checklist?.filter((item) => scriptIds.has(item.id)) ?? [];
      const failed = scriptItems.filter((item) => !item.passed);
      if (failed.length > 0) {
        const first = failed[0];
        toaster.error({
          title: "Script validation failed",
          description: first.message || first.label,
        });
        return { ok: false as const, result };
      }
      return { ok: true as const, result };
    } catch {
      return { ok: false as const, result: null };
    }
  }, [applyScript, runValidate, scriptText]);

  const handleScriptSaveAndValidate = useCallback(async () => {
    const outcome = await runScriptValidation();
    if (!outcome.ok) return outcome.result;
    setScriptValidatedText(persistedScriptText(applyScript, scriptText));
    toaster.success({ title: confirmCopy.scriptSavedAndValidated });
    return outcome.result;
  }, [applyScript, runScriptValidation, scriptText]);

  const handleInsertScriptExample = useCallback((code: string) => {
    setScriptText(wrapTransformBody(code));
    setEntryPoint(TRANSFORM_ENTRY_POINT);
    setScriptValidatedText("");
    setDryRunResult(null);
  }, []);

  const handlePickScriptFile = useCallback(
    async (file: File) => {
      let text = "";
      try {
        text = await file.text();
      } catch {
        toaster.error({
          title: "Failed to read file",
          action: {
            label: confirmCopy.retry,
            onClick: () => {
              void file.text().then((value) => {
                setScriptText(normalizeTransformScript(value));
                setEntryPoint(TRANSFORM_ENTRY_POINT);
              });
            },
          },
        });
        return;
      }
      const wrapped = normalizeTransformScript(text);
      if (
        new Blob([wrapped]).size > MAX_SCRIPT_BYTES ||
        wrapped.split("\n").length > MAX_SCRIPT_LINES
      ) {
        toaster.error({
          title: `Script exceeds ${MAX_SCRIPT_LINES} lines or ${MAX_SCRIPT_BYTES / 1024} KB`,
        });
        return;
      }
      if ((baseline?.scriptText ?? "") !== scriptText) {
        const ok = await confirm({
          title: confirmCopy.replaceScript.title,
          description: confirmCopy.replaceScript.description,
          cancelLabel: confirmCopy.replaceScript.cancelLabel,
          confirmLabel: confirmCopy.replaceScript.confirmLabel,
          tone: "danger",
          icon: "warning",
        });
        if (!ok) return;
      }
      setScriptText(wrapped);
      setApplyScript(true);
      setEntryPoint(TRANSFORM_ENTRY_POINT);
      setScriptNameFromUpload(true);
      setScriptFileMeta({ fileName: file.name, size: file.size });
      setScriptValidatedText("");
      setDryRunResult(null);
    },
    [baseline?.scriptText, scriptText],
  );

  const handleRemoveScript = useCallback(async () => {
    const ok = await confirm({
      title: confirmCopy.removeScript.title,
      description: confirmCopy.removeScript.description,
      cancelLabel: confirmCopy.removeScript.cancelLabel,
      confirmLabel: confirmCopy.removeScript.confirmLabel,
      tone: "danger",
      icon: "warning",
    });
    if (!ok) return;
    setScriptText("");
    setScriptFileMeta(null);
    setScriptNameFromUpload(false);
    setScriptValidatedText("");
    setApplyScript(false);
    setEntryPoint(TRANSFORM_ENTRY_POINT);
  }, []);

  const handleApplyScriptChange = useCallback(
    async (checked: boolean) => {
      if (checked) {
        setApplyScript(true);
        if (!scriptTextRef.current.trim()) {
          const seeded = wrapTransformBody("");
          scriptTextRef.current = seeded;
          setScriptText(seeded);
          setEntryPoint(TRANSFORM_ENTRY_POINT);
        }
        return;
      }
      if (unwrapTransformBody(scriptText).trim() || scriptFileMeta) {
        await handleRemoveScript();
        return;
      }
      setApplyScript(false);
    },
    [handleRemoveScript, scriptFileMeta, scriptText],
  );

  useEffect(() => {
    if (!open || !mappingJson) return;
    let cancelled = false;
    void (async () => {
      const sentScript = persistedScriptText(applyScript, scriptText);
      try {
        const result = await validateMut.mutateAsync({
          mapping_json: mappingJson,
          mapping_overrides: overridePayload(historyRef.current.present),
          script_text: sentScript,
          entry_point: TRANSFORM_ENTRY_POINT,
          destination_name_source: destSourceRef.current,
          destination_table_name: destNameRef.current,
        });
        if (cancelled) return;
        setValidateResult(result);
        setMarkedScriptText(sentScript);
      } catch {
        if (!cancelled) {
          setValidateResult(null);
          setMarkedScriptText(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // Auto-refresh column mapping preview when JSON is loaded or replaced.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- script/entry re-validated on Next
  }, [open, mappingJson]);

  const runDryRun = useCallback(async () => {
    try {
      const result = await dryRunMut.mutateAsync({
        mapping_json: mappingJson,
        mapping_overrides: overridePayload(history.present),
        script_text: persistedScriptText(applyScript, scriptText),
        entry_point: TRANSFORM_ENTRY_POINT,
        run_order: effectiveRunOrder,
        sample_size: DEFAULT_DRY_RUN_ROWS,
      });
      setDryRunResult(result);
      return result;
    } catch (err) {
      setDryRunResult(null);
      const message =
        typeof err === "object" &&
        err !== null &&
        "message" in err &&
        typeof (err as { message?: string }).message === "string"
          ? (err as { message: string }).message
          : "Dry run failed. Check SAP connectivity and your mapping or script.";
      toaster.error({
        title: "Dry run failed",
        description: message,
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void runDryRun();
          },
        },
      });
      throw err;
    }
  }, [
    applyScript,
    dryRunMut,
    mappingJson,
    scriptText,
    effectiveRunOrder,
    history.present,
  ]);

  const localDestInvalid = useMemo(() => {
    if (!destName.trim()) return "Destination table name cannot be empty.";
    if (!isValidSnowflakeDestinationName(destName)) {
      // Same identifier rules for Snowflake and Unity Catalog (v1).
      return isDatabricksConnector(destinationName ?? undefined)
        ? "Destination table name is not a valid Unity Catalog identifier."
        : "Destination table name is not a valid Snowflake identifier.";
    }
    if (collisionLabel) {
      const connector = validateResult?.destination?.collision?.connector_name;
      return connector
        ? `Destination table '${destName}' is already used by entity '${collisionLabel}' on connector '${connector}'.`
        : `Destination table '${destName}' is already used by entity '${collisionLabel}'.`;
    }
    return null;
  }, [
    collisionLabel,
    destName,
    destinationName,
    validateResult?.destination?.collision?.connector_name,
  ]);

  const existingSnowflakeTable = Boolean(
    validateResult?.destination?.existing_snowflake_table &&
      !confirmUseExisting,
  );

  const destBlocksNavigation = Boolean(
    destError || localDestInvalid || isEditingDest,
  );
  const destBlocksSave = Boolean(
    destBlocksNavigation || existingSnowflakeTable,
  );
  const targetBlocksNext = effectiveRows.some(
    (row) =>
      !row.targetLocked && targetNameError(row.target, effectiveRows, row.id),
  );
  const scriptBytes = new Blob([scriptText]).size;
  const scriptLines = scriptText ? scriptText.split("\n").length : 0;
  const scriptBody = unwrapTransformBody(scriptText);
  const defaultScriptName = defaultScriptFileName(
    mappingJson,
    parts.entityName,
  );
  const scriptChipName =
    scriptNameFromUpload && scriptFileMeta?.fileName
      ? scriptFileMeta.fileName
      : scriptText.trim()
        ? defaultScriptName
        : null;
  const scriptChipSize = scriptFileMeta?.size ?? scriptBytes;
  const transformRootIssue = useMemo(() => {
    if (!applyScript || !scriptText.trim()) return null;
    return validateTransformRoot(scriptText);
  }, [applyScript, scriptText]);
  const scriptEdited = useMemo(() => {
    if (!scriptBody.trim()) return false;
    const current = normalizeTransformScript(
      persistedScriptText(applyScript, scriptText),
    );
    const saved = normalizeTransformScript(scriptValidatedText);
    return current !== saved;
  }, [applyScript, scriptBody, scriptText, scriptValidatedText]);
  const editorScriptValidation = useMemo((): EditorValidation | null => {
    if (transformRootIssue) {
      return {
        passed: false,
        message: transformRootIssue.message,
        line: transformRootIssue.line,
      };
    }
    const checklistCurrent =
      markedScriptText === persistedScriptText(applyScript, scriptText);
    if (scriptEdited && !checklistCurrent) {
      return { passed: false, message: "Edited — save and validate" };
    }
    const scriptIds = new Set([
      "parse",
      "imports",
      "banned",
      "size",
      "entry_point",
    ]);
    const scriptItems =
      validateResult?.checklist?.filter((item) => scriptIds.has(item.id)) ?? [];
    if (scriptItems.length === 0) return null;
    const failed = scriptItems.find((item) => !item.passed);
    if (failed) {
      return {
        passed: false,
        message: failed.message || failed.label,
        line: failed.line,
      };
    }
    return { passed: true, message: "Validated" };
  }, [
    applyScript,
    markedScriptText,
    scriptEdited,
    scriptText,
    transformRootIssue,
    validateResult?.checklist,
  ]);
  const editorDiagnostics = useMemo((): EditorDiagnostic[] => {
    const items: EditorDiagnostic[] = [];
    if (transformRootIssue) {
      items.push({
        line: transformRootIssue.line,
        message: transformRootIssue.message,
        source: "editor",
      });
    }
    if (scriptBytes > MAX_SCRIPT_BYTES || scriptLines > MAX_SCRIPT_LINES) {
      items.push({
        line: scriptLines > MAX_SCRIPT_LINES ? scriptLines : 1,
        message: `Script exceeds ${MAX_SCRIPT_LINES} lines or ${MAX_SCRIPT_BYTES / 1024} KB (${scriptLines} lines, ${(scriptBytes / 1024).toFixed(1)} KB).`,
        source: "editor",
      });
    }
    const checklistCurrent =
      markedScriptText === persistedScriptText(applyScript, scriptText);
    if (checklistCurrent) {
      const scriptIds = new Set(["parse", "imports", "banned", "entry_point"]);
      for (const item of validateResult?.checklist ?? []) {
        if (!scriptIds.has(item.id) || item.passed) continue;
        const locations = item.all_errors?.length
          ? item.all_errors
          : [{ line: item.line, message: item.message || item.label }];
        for (const location of locations) {
          items.push({
            line: location.line || undefined,
            message: location.message || item.message || item.label,
            detail: item.label,
            source: "server",
          });
        }
      }
    }
    for (const error of dryRunResult?.errors ?? []) {
      items.push({
        line: scriptErrorLine(error.traceback, error.line),
        message: error.message,
        detail: error.traceback || undefined,
        source: "server",
      });
    }
    return items;
  }, [
    applyScript,
    dryRunResult?.errors,
    markedScriptText,
    scriptBytes,
    scriptLines,
    scriptText,
    transformRootIssue,
    validateResult?.checklist,
  ]);
  const scriptBlocksNext =
    scriptBytes > MAX_SCRIPT_BYTES ||
    scriptLines > MAX_SCRIPT_LINES ||
    Boolean(transformRootIssue);
  const noneMapped = !columnsRemainMapped(
    effectiveRows,
    counts,
    Boolean(mappingJson),
  );
  const stepBlocksNext =
    (step === 0 &&
      (destBlocksNavigation ||
        targetBlocksNext ||
        jsonErrors.length > 0 ||
        noneMapped)) ||
    (step === 1 && scriptBlocksNext);

  const currentBaseline = useMemo<MappingBaseline>(
    () => ({
      mappingJson,
      overrides: history.present,
      scriptText,
      entryPoint,
      runOrder: effectiveRunOrder,
      destName,
      destSource,
    }),
    [
      mappingJson,
      history.present,
      scriptText,
      entryPoint,
      effectiveRunOrder,
      destName,
      destSource,
    ],
  );

  const isDirty = useMemo(
    () => (baseline ? isMappingDirty(baseline, currentBaseline) : false),
    [baseline, currentBaseline],
  );

  const askLoadedRename = async (previous: string, next: string) => {
    const mode = await confirm({
      title: confirmCopy.renameDestination.title,
      description: confirmCopy.renameDestination.description(previous, next),
      cancelLabel: confirmCopy.renameDestination.cancelLabel,
      confirmLabel: confirmCopy.renameDestination.confirmLabel,
      icon: "warning",
      choices: confirmCopy.renameDestination.choices,
    });
    if (mode === "cancel") return null;
    return mode === "rename_existing" ? "rename_existing" : "create_new";
  };

  const buildPersistPayload = useCallback(
    (forcedRenameMode?: "create_new" | "rename_existing") => {
      let effectiveRename = forcedRenameMode || renameMode;
      if (!effectiveRename && columnAddMode === "full_reload") {
        effectiveRename = "create_new";
      }
      return {
        mapping_json: mappingJson,
        script_text: persistedScriptText(applyScript, scriptText),
        entry_point: TRANSFORM_ENTRY_POINT,
        run_order: effectiveRunOrder,
        mapping_overrides: overridePayload(history.present),
        destination_name_source: destSource,
        destination_table_name: destName,
        destination_rename_mode: effectiveRename,
        confirm_use_existing_table: confirmUseExisting,
      };
    },
    [
      applyScript,
      columnAddMode,
      confirmUseExisting,
      destName,
      destSource,
      effectiveRunOrder,
      history.present,
      mappingJson,
      renameMode,
      scriptText,
    ],
  );

  const flushDraftAutosave = useCallback(async () => {
    if (readOnly || !open) return;
    if (Date.now() < skipAutosaveUntilRef.current) return;
    if (!isDirty && !transformData?.draft) return;
    try {
      setDraftSaveLabel("Saving draft…");
      await draftSaveMut.mutateAsync(buildPersistPayload());
      setDraftSaveLabel("Draft saved");
      setBaseline(currentBaseline);
    } catch {
      setDraftSaveLabel("Draft save failed");
    }
  }, [
    buildPersistPayload,
    currentBaseline,
    draftSaveMut,
    isDirty,
    open,
    readOnly,
    transformData?.draft,
  ]);

  useEffect(() => {
    if (!open || readOnly) return;
    if (Date.now() < skipAutosaveUntilRef.current) return;
    if (!isDirty) return;
    const timer = window.setTimeout(() => {
      void flushDraftAutosave();
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [flushDraftAutosave, isDirty, open, readOnly, currentBaseline]);

  const handleValidateAndPublish = async (
    forcedRenameMode?: "create_new" | "rename_existing",
  ) => {
    let effectiveRename = forcedRenameMode || renameMode;
    if (!effectiveRename && columnAddMode === "full_reload") {
      effectiveRename = "create_new";
    }
    const nextName = destName.trim().toUpperCase();
    if (
      hasLoadedData &&
      previousSavedDest &&
      previousSavedDest !== nextName &&
      !effectiveRename
    ) {
      const mode = await askLoadedRename(previousSavedDest, nextName);
      if (!mode) return;
      effectiveRename = mode;
      setRenameMode(mode);
    }
    const tested = Boolean(
      dryRunResult &&
        (dryRunResult.errors?.length ?? 0) === 0 &&
        dryRunResult.rows_out >= 0,
    );
    if (!tested) {
      const ok = await confirm({
        title: confirmCopy.saveWithoutTest.title,
        description: confirmCopy.saveWithoutTest.description,
        cancelLabel: confirmCopy.saveWithoutTest.cancelLabel,
        confirmLabel: confirmCopy.saveWithoutTest.confirmLabel,
        icon: "warning",
      });
      if (!ok) return;
    }
    try {
      const result = await validateMut.mutateAsync({
        ...buildPersistPayload(effectiveRename ?? undefined),
        publish: true,
      });
      setValidateResult(result);
      setMarkedScriptText(persistedScriptText(applyScript, scriptText));
      if (!result.valid) {
        const first =
          result.errors?.[0]?.message ||
          result.publish_error ||
          "Validation failed";
        if (result.requires_rename_confirmation) {
          const mode = await askLoadedRename(previousSavedDest, nextName);
          if (!mode) return;
          setRenameMode(mode);
          await handleValidateAndPublish(mode);
          return;
        }
        toaster.error({ title: first });
        return;
      }
      if (result.published) {
        sessionStorage.removeItem(draftKey);
        setBaseline(currentBaseline);
        setScriptValidatedText(persistedScriptText(applyScript, scriptText));
        toaster.success({
          title: confirmCopy.published(result.published.version),
        });
        onClose();
        return;
      }
      toaster.error({
        title:
          result.publish_error || "Validate passed but publish did not run",
      });
    } catch {
      toaster.error({
        title: "Validate failed",
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void handleValidateAndPublish(effectiveRename ?? undefined);
          },
        },
      });
    }
  };

  const handleRemove = async () => {
    const ok = await confirm({
      title: confirmCopy.removeMapping.title,
      description: confirmCopy.removeMapping.description,
      cancelLabel: confirmCopy.removeMapping.cancelLabel,
      confirmLabel: confirmCopy.removeMapping.confirmLabel,
      tone: "danger",
      icon: "danger",
    });
    if (!ok) return;
    try {
      await deactivateMut.mutateAsync();
      toaster.success({ title: "Transform removed" });
      onClose();
    } catch {
      toaster.error({
        title: "Could not remove mapping",
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void handleRemove();
          },
        },
      });
    }
  };

  const handleReset = async () => {
    const impact = resetToJsonImpact(draft);
    const ok = await confirm({
      title: confirmCopy.reset.title,
      description: confirmCopy.reset.description(
        impact.restored,
        impact.discardedAdded,
        impact.renames,
      ),
      cancelLabel: confirmCopy.reset.cancelLabel,
      confirmLabel: confirmCopy.reset.confirmLabel,
      icon: "warning",
    });
    if (!ok) return;
    commitOverrides(snapshotOf(resetToJson(draft)));
  };

  const removeMappingJson = async () => {
    if (readOnly) return;
    const currentOverrides = historyRef.current.present;
    let nextOverrides = emptyOverrides();
    if (hasManualOverrides(currentOverrides)) {
      const choice = await confirm({
        title: confirmCopy.replaceJson.title,
        description: confirmCopy.replaceJson.description,
        cancelLabel: confirmCopy.replaceJson.cancelLabel,
        confirmLabel: confirmCopy.replaceJson.confirmLabel,
        icon: "warning",
        alternate: {
          label: confirmCopy.replaceJson.alternateLabel,
          tone: "danger",
        },
      });
      if (choice === "cancel") return;
      if (choice === "confirm") {
        nextOverrides = keepApplicableOverrides(
          currentOverrides,
          {},
          sapColumns,
        );
      }
    }
    setMappingJson(null);
    setJsonFileMeta(null);
    setJsonErrors([]);
    commitOverrides(nextOverrides);
    applyResolved(null, "DEFAULT");
  };

  const confirmColumnAdd = () => {
    if (!hasLoadedData || columnAddModeRef.current)
      return Promise.resolve(true);
    if (columnAddRequest.current) return columnAddRequest.current;
    columnAddRequest.current = (async () => {
      const choice = await confirm({
        title: confirmCopy.addColumns.title,
        description: confirmCopy.addColumns.description,
        cancelLabel: confirmCopy.addColumns.cancelLabel,
        confirmLabel: confirmCopy.addColumns.confirmLabel,
        icon: "warning",
        choices: confirmCopy.addColumns.choices,
      });
      columnAddRequest.current = null;
      if (choice === "cancel") return false;
      const mode =
        choice === "full_reload" ? "full_reload" : "new_columns_only";
      columnAddModeRef.current = mode;
      setColumnAddMode(mode);
      if (mode === "full_reload") {
        setRenameMode((current) => current ?? "create_new");
      }
      return true;
    })();
    return columnAddRequest.current;
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (open) {
      openerRef.current = document.activeElement as HTMLElement | null;
      return;
    }
    openerRef.current?.focus?.();
  }, [open]);

  useEffect(() => {
    if (!open || !isDirty) return;
    const onUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [open, isDirty]);

  useEffect(() => {
    if (!open || !baseline) return;
    const keepSession = isDirty || dryRunResult !== null || step > 0;
    if (!keepSession) {
      sessionStorage.removeItem(draftKey);
      return;
    }
    sessionStorage.setItem(
      draftKey,
      JSON.stringify({
        mappingJson,
        overrides: history.present,
        scriptText,
        destName,
        destSource,
        fileName: jsonFileMeta?.fileName,
        fileSize: jsonFileMeta?.size,
        scriptFileName: scriptChipName,
        scriptFileSize: scriptChipSize,
        step,
        applyScript,
        dryRunResult,
        scriptValidatedText,
        scriptNameFromUpload,
        runOrder: effectiveRunOrder,
      }),
    );
  }, [
    open,
    baseline,
    isDirty,
    draftKey,
    mappingJson,
    history.present,
    scriptText,
    destName,
    destSource,
    jsonFileMeta,
    scriptChipName,
    scriptChipSize,
    step,
    applyScript,
    dryRunResult,
    scriptValidatedText,
    scriptNameFromUpload,
    effectiveRunOrder,
  ]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (confirmOpen) return;
      if (event.key === "Escape" && modalExpanded) {
        event.preventDefault();
        event.stopPropagation();
        setModalExpanded(false);
        return;
      }
      if (readOnly) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        setHistory((current) => undoHistory(current));
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        setHistory((current) => redoHistory(current));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, readOnly, modalExpanded, confirmOpen]);

  const requestClose = async (intent: "close" | "back" = "close") => {
    if (closeGuard.current) return;
    if (intent === "back") {
      setStep(0);
      return;
    }
    if (intent === "close" && modalExpanded) {
      setModalExpanded(false);
      return;
    }
    if (isDirty && !readOnly) {
      closeGuard.current = true;
      await flushDraftAutosave();
      closeGuard.current = false;
      if (!mountedRef.current) return;
    }
    onClose();
  };

  const hasActive = Boolean(transformData?.active?.is_active);
  const hasServerDraft = Boolean(transformData?.draft);
  const shownError = destError || localDestInvalid;

  return (
    <>
      <Dialog.Root
        lazyMount
        open={open}
        size="lg"
        closeOnEscape={!modalExpanded && !confirmOpen}
        onOpenChange={(details) => {
          if (!details.open) {
            if (confirmOpen) return;
            void requestClose();
          }
        }}
        aria-labelledby="transform-modal-title"
      >
        <Portal>
          <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
          <Dialog.Positioner>
            <Dialog.Content
              borderRadius="xl"
              maxW={modalExpanded ? "95vw" : "1120px"}
              w="100%"
              maxH={modalExpanded ? "92vh" : "90vh"}
              display="flex"
              flexDirection="column"
            >
              <Dialog.Header bg="gray.50" flexShrink={0}>
                <Dialog.Title
                  id="transform-modal-title"
                  color="brand.800"
                  fontSize="lg"
                >
                  Mapping and transform: {modalTitle}
                </Dialog.Title>
                <Flex gap={1}>
                  <CloseButton
                    size="sm"
                    aria-label={
                      modalExpanded
                        ? "Collapse mapping dialog"
                        : "Expand mapping dialog"
                    }
                    onClick={() => setModalExpanded((value) => !value)}
                  >
                    {modalExpanded ? <FiMinimize2 /> : <FiMaximize2 />}
                  </CloseButton>
                  <Dialog.CloseTrigger asChild>
                    <CloseButton size="sm" />
                  </Dialog.CloseTrigger>
                </Flex>
              </Dialog.Header>
              <Box aria-live="polite" position="absolute" left="-9999px">
                {announce}
              </Box>

              <Flex
                px={6}
                py={3}
                gap={2}
                flexShrink={0}
                borderBottomWidth={1}
                align="center"
                flexWrap="wrap"
              >
                {STEPS.map((label, idx) => {
                  const hasIssue =
                    (idx === 0 &&
                      (destBlocksNavigation ||
                        targetBlocksNext ||
                        jsonErrors.length > 0)) ||
                    (idx === 1 && scriptBlocksNext);
                  return (
                    <Badge
                      key={label}
                      colorPalette={step === idx ? "brand" : "gray"}
                      variant={step === idx ? "solid" : "outline"}
                      cursor="pointer"
                      onClick={() => setStep(idx)}
                    >
                      {idx + 1}. {label}
                      {hasIssue ? " •" : ""}
                    </Badge>
                  );
                })}
                <Flex ml="auto" gap={2} align="center">
                  {/* IDE shows one status: Draft while editing unvalidated work, else Validated */}
                  {hasServerDraft || isDirty ? (
                    <Badge colorPalette="orange" variant="subtle">
                      {confirmCopy.draftBadge}
                    </Badge>
                  ) : hasActive && publishedVersion !== null ? (
                    <Badge colorPalette="green" variant="subtle">
                      {confirmCopy.validatedBadge(publishedVersion)}
                    </Badge>
                  ) : (
                    <Badge colorPalette="gray" variant="subtle">
                      No validated version
                    </Badge>
                  )}
                  {draftSaveLabel && (hasServerDraft || isDirty) && (
                    <Text fontSize="xs" color="gray.500">
                      {draftSaveLabel}
                    </Text>
                  )}
                </Flex>
              </Flex>

              <Dialog.Body overflowY="auto" flex="1" px={6} py={4}>
                {step === 0 && (
                  <VStack align="stretch" gap={4}>
                    {readOnly && (
                      <Text fontSize="xs" color="orange.700">
                        You can view this mapping. Editing requires table edit
                        permission.
                      </Text>
                    )}
                    <JsonFileChip
                      fileName={
                        mappingJson
                          ? jsonFileMeta?.fileName || "Saved mapping"
                          : null
                      }
                      size={jsonFileMeta?.size || 0}
                      mappingCount={
                        Array.isArray(mappingJson?.field_mapping)
                          ? mappingJson.field_mapping.length
                          : counts.mapped
                      }
                      parsing={parsing}
                      disabled={readOnly}
                      onFile={(file) => void handleFileDrop(file)}
                      onRemove={() => {
                        void removeMappingJson();
                      }}
                    />
                    {jsonErrors.map((error) => (
                      <Text key={error.path} fontSize="xs" color="red.600">
                        {error.path}: {error.message}
                      </Text>
                    ))}

                    <DestinationTableField
                      destinationTableName={destName}
                      destinationConnectionName={destinationName}
                      jsonTableName={jsonRawName}
                      source={destSource}
                      defaultDisplayName={defaultDisplay || modalTitle}
                      isEditing={isEditingDest}
                      editValue={editDestValue}
                      error={shownError}
                      warnings={destWarnings}
                      existingSnowflakeTable={existingSnowflakeTable}
                      confirmUseExisting={confirmUseExisting}
                      onUseDefault={() => {
                        applyResolved(mappingJson, "DEFAULT");
                        setConfirmUseExisting(false);
                      }}
                      onStartEdit={() => {
                        setIsEditingDest(true);
                        setEditDestValue(destName);
                      }}
                      onEditChange={setEditDestValue}
                      onCommitEdit={() => {
                        const resolved = applyResolved(
                          mappingJson,
                          "MANUAL",
                          editDestValue,
                        );
                        if (!resolved.valid) return;
                        setIsEditingDest(false);
                        setConfirmUseExisting(false);
                      }}
                      onCancelEdit={() => setIsEditingDest(false)}
                      onConfirmUseExisting={() => setConfirmUseExisting(true)}
                      onChooseAnotherName={() => {
                        setIsEditingDest(true);
                        setEditDestValue(destName);
                      }}
                    />
                    <Flex gap={2} flexWrap="wrap">
                      {(
                        [
                          [`${counts.mapped} mapped`, "gray"],
                          [`${counts.removed} removed`, "red"],
                          [`${counts.added} added`, "purple"],
                          [`${counts.notSelected} not selected`, "gray"],
                          [`${counts.fromScript} from script`, "gray"],
                        ] as const
                      ).map(([label, palette]) => (
                        <Badge
                          key={label}
                          size="sm"
                          variant="outline"
                          colorPalette={palette}
                          borderRadius="full"
                          bg="white"
                        >
                          {label}
                        </Badge>
                      ))}
                    </Flex>
                    {noneMapped && (
                      <Text fontSize="sm" color="red.600">
                        At least one column must stay mapped.
                      </Text>
                    )}

                    <Flex gap={2} align="center" wrap="wrap">
                      <Input
                        placeholder="Search SAP or target column"
                        size="sm"
                        flex="1"
                        minW="180px"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                      <Flex
                        borderWidth="1px"
                        borderColor="gray.200"
                        borderRadius="md"
                        overflow="hidden"
                      >
                        {(
                          [
                            ["all", "All"],
                            ["unmapped_source", "Unmapped source"],
                            ["unmapped_target", "Unmapped target"],
                            [
                              "warnings",
                              `Warnings ${
                                effectiveRows.filter((row) =>
                                  [
                                    "Narrowed",
                                    "Date check",
                                    "Not in SAP",
                                  ].includes(row.status),
                                ).length
                              }`,
                            ],
                            ["removed", `Removed ${removedRows.length}`],
                          ] as const
                        ).map(([key, label]) => (
                          <Button
                            key={key}
                            size="xs"
                            borderRadius={0}
                            variant={filter === key ? "solid" : "ghost"}
                            colorPalette={filter === key ? "brand" : "gray"}
                            onClick={() => setFilter(key)}
                          >
                            {label}
                          </Button>
                        ))}
                      </Flex>
                      <Flex
                        borderWidth="1px"
                        borderColor="gray.200"
                        borderRadius="md"
                        overflow="hidden"
                      >
                        <Button
                          size="xs"
                          borderRadius={0}
                          variant={viewMode === "mapper" ? "solid" : "ghost"}
                          colorPalette="brand"
                          onClick={() => setViewMode("mapper")}
                        >
                          Mapper
                        </Button>
                        <Button
                          size="xs"
                          borderRadius={0}
                          variant={viewMode === "table" ? "solid" : "ghost"}
                          colorPalette="brand"
                          onClick={() => setViewMode("table")}
                        >
                          Table
                        </Button>
                      </Flex>
                      <IconButton
                        size="xs"
                        variant="ghost"
                        aria-label="Undo"
                        disabled={readOnly || history.past.length === 0}
                        onClick={() => {
                          setHistory((current) => undoHistory(current));
                        }}
                      >
                        <FiRotateCcw />
                      </IconButton>
                      <IconButton
                        size="xs"
                        variant="ghost"
                        aria-label="Redo"
                        disabled={readOnly || history.future.length === 0}
                        onClick={() => {
                          setHistory((current) => redoHistory(current));
                        }}
                      >
                        <FiRotateCw />
                      </IconButton>
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={readOnly}
                        onClick={() => void handleReset()}
                      >
                        Reset to JSON
                      </Button>
                      <IconButton
                        size="xs"
                        variant="ghost"
                        aria-label={
                          modalExpanded
                            ? "Collapse mapping dialog"
                            : "Expand mapping dialog"
                        }
                        onClick={() => setModalExpanded((value) => !value)}
                      >
                        {modalExpanded ? <FiMinimize2 /> : <FiMaximize2 />}
                      </IconButton>
                    </Flex>

                    {!mappingJson && sapColumns.length === 0 && (
                      <Text fontSize="sm" color="gray.500">
                        Using same names as SAP. Add a mapping JSON or turn on
                        Apply routine on the Script step to customise.
                      </Text>
                    )}

                    {viewMode === "mapper" ? (
                      <Box
                        h={modalExpanded ? "min(62vh, 560px)" : "420px"}
                        flexShrink={0}
                      >
                        <ColumnMapper
                          rows={effectiveRows}
                          removedRows={removedRows}
                          sapColumns={sapColumns}
                          jsonRaw={mappingJson}
                          search={search}
                          filter={filter}
                          readOnly={readOnly}
                          paneMaxHeight="100%"
                          onToggleJsonSource={(name, checked) => {
                            const row = checked
                              ? undefined
                              : effectiveRows.find(
                                  (item) =>
                                    item.id.startsWith("json:") &&
                                    item.source === name,
                                );
                            const next = snapshotOf(
                              setJsonSourcesIncluded(draft, [name], checked),
                            );
                            if (
                              JSON.stringify(next) ===
                              JSON.stringify(history.present)
                            ) {
                              return;
                            }
                            commitOverrides(next);
                            if (!checked) notifyRemoval(row);
                          }}
                          onToggleSource={(name, checked) => {
                            if (!checked) {
                              commitOverrides(
                                snapshotOf(
                                  toggleAddedSource(draft, name, false),
                                ),
                              );
                              return;
                            }
                            void (async () => {
                              if (!(await confirmColumnAdd())) return;
                              commitOverrides(
                                snapshotOf(
                                  toggleAddedSource(
                                    currentMappingDraft(),
                                    name,
                                    true,
                                  ),
                                ),
                              );
                            })();
                          }}
                          onRepoint={(targetId, source) =>
                            commitOverrides(
                              snapshotOf(
                                repointMapping(draft, targetId, source),
                              ),
                            )
                          }
                          onReorder={(orderedIds) =>
                            commitOverrides(
                              snapshotOf(
                                reorderMappedColumns(
                                  currentMappingDraft(),
                                  orderedIds,
                                ),
                              ),
                            )
                          }
                          onRename={(targetId, name) =>
                            commitOverrides(
                              snapshotOf(renameTarget(draft, targetId, name)),
                            )
                          }
                          onClear={(targetId) => {
                            const next = snapshotOf(
                              clearUserMapping(draft, targetId),
                            );
                            if (
                              JSON.stringify(next) ===
                              JSON.stringify(history.present)
                            ) {
                              return;
                            }
                            const row = effectiveRows.find(
                              (item) => item.id === targetId,
                            );
                            commitOverrides(next);
                            notifyRemoval(row);
                          }}
                          onAddTarget={(source) => {
                            void (async () => {
                              if (!(await confirmColumnAdd())) return;
                              commitOverrides(
                                snapshotOf(
                                  addUserTarget(currentMappingDraft(), source),
                                ),
                              );
                            })();
                          }}
                          onSelectAll={(names) => {
                            void (async () => {
                              if (!(await confirmColumnAdd())) return;
                              const next = names.reduce(
                                (current, name) =>
                                  toggleAddedSource(current, name, true),
                                currentMappingDraft(),
                              );
                              commitOverrides(snapshotOf(next));
                            })();
                          }}
                          onSelectAllJson={() => {
                            const next = snapshotOf(
                              setJsonSourcesIncluded(
                                draft,
                                [...jsonSourceNames(mappingJson)],
                                true,
                              ),
                            );
                            commitOverrides(next);
                          }}
                          onClearJson={() => {
                            const names = [...jsonSourceNames(mappingJson)];
                            const keyRow = effectiveRows.find(
                              (row) =>
                                row.id.startsWith("json:") &&
                                names.includes(row.source || "") &&
                                keyRemovalWarning(row, sapColumns),
                            );
                            const next = snapshotOf(
                              setJsonSourcesIncluded(draft, names, false),
                            );
                            if (
                              JSON.stringify(next) ===
                              JSON.stringify(history.present)
                            ) {
                              return;
                            }
                            commitOverrides(next);
                            if (keyRow) notifyRemoval(keyRow);
                          }}
                          onRestore={(targetId) => {
                            commitOverrides(
                              snapshotOf(restoreJsonMapping(draft, targetId)),
                            );
                          }}
                          onRestoreAll={() => {
                            commitOverrides(
                              snapshotOf(restoreAllJsonMappings(draft)),
                            );
                          }}
                          onAnnounce={setAnnounce}
                          confirm={confirm}
                        />
                      </Box>
                    ) : (
                      mappingTable.length > 0 && (
                        <VirtualMappingTable
                          rows={mappingTable}
                          search={search}
                          filter={filter}
                        />
                      )
                    )}

                    {filterSummary && (
                      <Text fontSize="xs" color="gray.600">
                        {filterSummary}
                      </Text>
                    )}
                    {entityMismatch && (
                      <Text fontSize="xs" color="orange.600">
                        JSON source.objectname does not match this entity after
                        normalising the letter O and the digit 0.
                      </Text>
                    )}

                    {validateResult?.warnings?.map((w) => (
                      <Text key={w.message} fontSize="xs" color="orange.600">
                        {w.message}
                      </Text>
                    ))}
                  </VStack>
                )}

                {step === 1 && (
                  <VStack align="stretch" gap={4}>
                    <Flex align="center" justify="space-between" gap={4}>
                      <Box>
                        <Text fontSize="sm" fontWeight="semibold">
                          Apply routine
                        </Text>
                        <Text fontSize="xs" color="gray.500">
                          Turn this on to upload or write a Python routine.
                          Leave it off to load with column mapping only. A
                          mapping JSON is optional.
                        </Text>
                      </Box>
                      <Switch.Root
                        checked={applyScript}
                        disabled={readOnly}
                        colorPalette="brand"
                        aria-label="Apply routine"
                        onCheckedChange={({ checked }) => {
                          void handleApplyScriptChange(checked);
                        }}
                      >
                        <Switch.HiddenInput />
                        <Switch.Control />
                      </Switch.Root>
                    </Flex>
                    {applyScript && (
                      <>
                        <ScriptFileChip
                          fileName={scriptChipName}
                          size={scriptChipSize}
                          lineCount={scriptLines}
                          edited={scriptEdited}
                          disabled={readOnly}
                          onFile={(file) => {
                            void handlePickScriptFile(file);
                          }}
                          onRemove={() => {
                            void handleRemoveScript();
                          }}
                        />
                        <ExpandableCodeEditor
                          value={scriptText}
                          fileName={scriptChipName || defaultScriptName}
                          exportText={
                            scriptText.trim()
                              ? scriptText
                              : wrapTransformBody("")
                          }
                          schemaFolds={scriptSchemaFolds}
                          examples={scriptExamples}
                          onInsertExample={handleInsertScriptExample}
                          onValidate={() => {
                            void handleScriptSaveAndValidate();
                          }}
                          validateLabel="Save and validate"
                          validateDisabled={
                            readOnly ||
                            !scriptBody.trim() ||
                            Boolean(transformRootIssue)
                          }
                          validating={validateMut.isPending}
                          validation={editorScriptValidation}
                          diagnostics={editorDiagnostics}
                          readOnly={readOnly}
                          compact
                          onChange={(next) => {
                            if (next === scriptText) return;
                            setScriptText(next);
                            setEntryPoint(TRANSFORM_ENTRY_POINT);
                            setDryRunResult(null);
                          }}
                        />
                      </>
                    )}
                  </VStack>
                )}

                {step === 2 && (
                  <VStack align="stretch" gap={4}>
                    <DestinationTableField
                      destinationTableName={destName}
                      destinationConnectionName={destinationName}
                      jsonTableName={jsonRawName}
                      source={destSource}
                      defaultDisplayName={defaultDisplay || modalTitle}
                      isEditing={false}
                      editValue={destName}
                      error={null}
                      warnings={[]}
                      existingSnowflakeTable={false}
                      confirmUseExisting
                      readOnly
                      onUseDefault={() => undefined}
                      onStartEdit={() => undefined}
                      onEditChange={() => undefined}
                      onCommitEdit={() => undefined}
                      onCancelEdit={() => undefined}
                      onConfirmUseExisting={() => undefined}
                      onChooseAnotherName={() => undefined}
                    />
                    <Box>
                      <Text fontSize="sm" fontWeight="semibold" mb={1}>
                        Run order
                      </Text>
                      <Text fontSize="sm" color="gray.600">
                        {mappingJson
                          ? applyScript
                            ? "JSON mapping is applied first, then the script."
                            : "JSON mapping is applied. No script will run."
                          : applyScript
                            ? "No JSON mapping. The script runs directly on the SAP extract."
                            : "No JSON mapping and no script. Rows load from the SAP extract."}
                      </Text>
                    </Box>
                    {applyScript && (
                      <>
                        {mappingJson &&
                          (
                            mappingJson.transformation as
                              | { type?: string }
                              | undefined
                          )?.type === "NONE" &&
                          unwrapTransformBody(scriptText).trim() && (
                            <Text fontSize="xs" color="orange.600">
                              JSON says no transformation, script found
                            </Text>
                          )}
                      </>
                    )}
                    <Flex gap={2} align="center" flexWrap="wrap">
                      <Text fontSize="sm" color="gray.600">
                        SAP sample: {DEFAULT_DRY_RUN_ROWS} rows
                      </Text>
                      <Button
                        colorPalette="brand"
                        size="sm"
                        loading={dryRunMut.isPending}
                        disabled={Boolean(transformRootIssue)}
                        onClick={runDryRun}
                      >
                        Run dry run
                      </Button>
                    </Flex>
                    {transformRootIssue && (
                      <Text fontSize="xs" color="red.600">
                        {transformRootIssue.message}
                        {transformRootIssue.line
                          ? ` (line ${transformRootIssue.line})`
                          : ""}
                      </Text>
                    )}
                    {dryRunResult && (
                      <>
                        <Text fontSize="sm">
                          Rows in: {dryRunResult.rows_in} · Rows out:{" "}
                          {dryRunResult.rows_out} · Columns:{" "}
                          {(dryRunResult.columns_out ?? []).length} /{" "}
                          {(dryRunResult.expected_columns ?? []).length}
                        </Text>
                        {(dryRunResult.warnings ?? []).map((w) => (
                          <Text
                            key={w.message}
                            fontSize="xs"
                            color="orange.600"
                          >
                            {w.message}
                          </Text>
                        ))}
                        {(dryRunResult.errors ?? []).map((error) => {
                          const line = scriptErrorLine(
                            error.traceback,
                            error.line,
                          );
                          return (
                            <Text
                              key={error.message}
                              fontSize="xs"
                              color="red.600"
                            >
                              {error.message}
                              {line ? ` (line ${line})` : ""}
                              {error.traceback ? (
                                <Box
                                  as="pre"
                                  mt={1}
                                  whiteSpace="pre-wrap"
                                  fontFamily="mono"
                                >
                                  {error.traceback}
                                </Box>
                              ) : null}
                            </Text>
                          );
                        })}
                        {(dryRunResult.input_preview?.length ?? 0) > 0 ? (
                          <ExpandablePreviewTable
                            title="Input sample (mapped SAP columns)"
                            rows={dryRunResult.input_preview ?? []}
                          />
                        ) : (
                          dryRunResult.rows_in > 0 && (
                            <Text fontSize="xs" color="gray.600">
                              No mapped source columns were available for the
                              input preview.
                            </Text>
                          )
                        )}
                        {(dryRunResult.output_preview?.length ?? 0) > 0 && (
                          <ExpandablePreviewTable
                            title="Target preview (__target_df)"
                            rows={dryRunResult.output_preview ?? []}
                          />
                        )}
                        {dryRunResult.rows_out > 0 &&
                          (dryRunResult.output_preview?.length ?? 0) <
                            dryRunResult.rows_out && (
                            <Text fontSize="xs" color="gray.500">
                              Preview shows the first{" "}
                              {dryRunResult.output_preview?.length ?? 0} of{" "}
                              {dryRunResult.rows_out} output rows.
                            </Text>
                          )}
                      </>
                    )}
                  </VStack>
                )}
              </Dialog.Body>

              <Dialog.Footer
                bg="gray.50"
                flexShrink={0}
                justifyContent="space-between"
              >
                <Flex gap={2} align="center">
                  {hasActive && (
                    <Link color="red.500" fontSize="sm" onClick={handleRemove}>
                      Remove mapping
                    </Link>
                  )}
                </Flex>
                <Flex gap={2}>
                  {step > 0 && (
                    <Button
                      variant="outline"
                      onClick={() => {
                        if (step === 1) void requestClose("back");
                        else setStep(step - 1);
                      }}
                    >
                      Back
                    </Button>
                  )}
                  <Button variant="outline" onClick={() => void requestClose()}>
                    Cancel
                  </Button>
                  {step === 0 && stepBlocksNext && (
                    <Text fontSize="xs" color="red.600" maxW="280px">
                      {isEditingDest
                        ? "Apply or cancel the destination table name edit."
                        : targetBlocksNext
                          ? "Fix invalid mapped column names."
                          : jsonErrors.length > 0
                            ? "Fix mapping JSON errors."
                            : noneMapped
                              ? "At least one column must stay mapped."
                              : "Fix the destination table name."}
                    </Text>
                  )}
                  {step < 2 ? (
                    <Button
                      colorPalette="brand"
                      loading={validateMut.isPending}
                      disabled={stepBlocksNext}
                      onClick={async () => {
                        if (stepBlocksNext) return;
                        try {
                          const result = await runValidate();
                          if (step === 0) {
                            if (result.destination?.error) return;
                            if (result.destination?.collision) return;
                          }
                          setStep(step + 1);
                        } catch {
                          /* toast shown in runValidate */
                        }
                      }}
                    >
                      Next
                    </Button>
                  ) : (
                    <Button
                      colorPalette="brand"
                      disabled={destBlocksSave || readOnly}
                      loading={validateMut.isPending}
                      onClick={() => void handleValidateAndPublish()}
                    >
                      Validate
                    </Button>
                  )}
                </Flex>
              </Dialog.Footer>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
      <ConfirmHost />
    </>
  );
};

export default TransformModal;
