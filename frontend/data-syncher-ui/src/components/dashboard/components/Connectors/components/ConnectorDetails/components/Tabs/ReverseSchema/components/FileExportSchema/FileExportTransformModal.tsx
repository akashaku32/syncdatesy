import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  IconButton,
  Link,
  Portal,
  Switch,
  Text,
  VStack,
} from "@chakra-ui/react";

import { FiMaximize2, FiMinimize2 } from "react-icons/fi";

import {
  ConfirmHost,
  confirm,
  useConfirmPending,
} from "@/components/ui/confirm";
import { toaster } from "@/components/ui/toaster";
import {
  DEFAULT_DRY_RUN_ROWS,
  MAX_SCRIPT_BYTES,
  MAX_SCRIPT_LINES,
} from "@/constants/transform-limits";
import usePermissions from "@/hooks/usePermissions";
import {
  transformQueryKey,
  useDeactivateTransform,
  useDryRunSourceScript,
  useEntityTransform,
  useSaveDraftTransform,
  useValidatePublishSourceScript,
} from "@/queryOptions/connector/schema/transform/useEntityTransform";
import {
  type ConnectorTable,
  type TransformDryRunResponse,
  type TransformValidateResponse,
} from "@/types/connectors";

import ExpandableCodeEditor, {
  type EditorDiagnostic,
  type EditorValidation,
  type SchemaFold,
} from "../../../Schema/TransformModal/ExpandableCodeEditor";
import ScriptFileChip from "../../../Schema/TransformModal/ScriptFileChip";
import { confirmCopy } from "../../../Schema/TransformModal/confirmCopy";
import { buildScriptExamples } from "../../../Schema/TransformModal/scriptExamples";
import {
  RUN_ORDER_FILTER_SCRIPT_MAPPING,
  TRANSFORM_ENTRY_POINT,
  coerceScriptSchemaDtype,
  normalizeTransformScript,
  persistedScriptText,
  scriptErrorLine,
  unwrapTransformBody,
  validateTransformRoot,
  wrapTransformBody,
} from "../../../Schema/TransformModal/scriptScaffold";
import FileExportPreviewTable from "./FileExportPreviewTable";
import { useQueryClient } from "@tanstack/react-query";

interface FileExportTransformModalProps {
  open: boolean;
  onClose: () => void;
  connectionId: number;
  tableItem: ConnectorTable;
}

const STEPS = ["Script", "Review and test"];

const EMPTY_OVERRIDES = {
  addedSources: [],
  removedTargets: [],
  removedJsonMappings: [],
  mappings: [],
  targetOrder: [],
};

const FileExportTransformModal = ({
  open,
  onClose,
  connectionId,
  tableItem,
}: FileExportTransformModalProps) => {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [modalExpanded, setModalExpanded] = useState(false);
  const [scriptText, setScriptText] = useState("");
  const [applyScript, setApplyScript] = useState(false);
  const [scriptNameFromUpload, setScriptNameFromUpload] = useState(false);
  const [scriptFileMeta, setScriptFileMeta] = useState<{
    fileName: string;
    size: number;
  } | null>(null);
  const [scriptValidatedText, setScriptValidatedText] = useState("");
  const [markedScriptText, setMarkedScriptText] = useState<string | null>(null);
  const [validateResult, setValidateResult] =
    useState<TransformValidateResponse | null>(null);
  const [dryRunResult, setDryRunResult] =
    useState<TransformDryRunResponse | null>(null);
  const [draftSaveLabel, setDraftSaveLabel] = useState<string | null>(null);
  const [baselineScriptText, setBaselineScriptText] = useState("");

  const scriptTextRef = useRef(scriptText);
  const skipAutosaveUntilRef = useRef(0);
  const closeGuard = useRef(false);
  const { can } = usePermissions();
  const readOnly = !can("can_edit_tables");
  const confirmOpen = useConfirmPending();
  const runValidateRef = useRef<
    () => Promise<TransformValidateResponse | null>
  >(() => Promise.resolve(null));
  const runDryRunRef = useRef<() => Promise<TransformDryRunResponse | null>>(
    () => Promise.resolve(null),
  );

  useEffect(() => {
    scriptTextRef.current = scriptText;
  }, [scriptText]);

  const { data: transformData } = useEntityTransform(
    connectionId,
    tableItem.table,
  );
  // File exports use source-side Python only. Keep the generic entity
  // transform hooks for the SAP mapping modal; these routes intentionally do
  // not send mapping_json or mapping_overrides to the backend.
  const publishMut = useValidatePublishSourceScript(
    connectionId,
    tableItem.table,
  );
  const dryRunMut = useDryRunSourceScript(connectionId, tableItem.table);
  const draftSaveMut = useSaveDraftTransform(connectionId, tableItem.table);
  const deactivateMut = useDeactivateTransform(connectionId, tableItem.table);

  const publishedVersion = transformData?.active?.version ?? null;
  const hasActive = Boolean(transformData?.active);
  const draftHasScript = Boolean(transformData?.draft?.script_text?.trim());
  const activeHasScript = Boolean(transformData?.active?.script_text?.trim());
  const hasServerDraft =
    draftHasScript || (Boolean(transformData?.draft) && !activeHasScript);
  const draftKey = `transform-draft:${connectionId}:${tableItem.table}`;
  const loadedKeyRef = useRef("");

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (!open) {
      loadedKeyRef.current = "";
      return;
    }
    const loadKey = `${tableItem.table}:a${transformData?.active?.version ?? (transformData?.active ? "act" : "none")}:d${transformData?.draft?.version ?? (transformData?.draft ? "drf" : "none")}:${transformData?.draft?.script_sha256 ?? ""}`;
    if (loadedKeyRef.current === loadKey) return;
    loadedKeyRef.current = loadKey;

    const draftScript = transformData?.draft?.script_text?.trim() || "";
    const activeScript = transformData?.active?.script_text?.trim() || "";
    const sourceRow =
      (draftScript
        ? transformData?.draft
        : activeScript
          ? transformData?.active
          : (transformData?.draft ?? transformData?.active)) ?? null;
    const serverScript =
      sourceRow?.script_text?.trim() || draftScript || activeScript;

    let sessionScript = "";
    let sessionApply: boolean | undefined = undefined;
    const stored = sessionStorage.getItem(draftKey);
    if (stored) {
      try {
        const parsed = JSON.parse(stored) as {
          scriptText?: string;
          applyScript?: boolean;
        };
        if (typeof parsed.scriptText === "string" && parsed.scriptText.trim()) {
          sessionScript = parsed.scriptText.trim();
        }
        if (typeof parsed.applyScript === "boolean") {
          sessionApply = parsed.applyScript;
        }
      } catch {
        sessionStorage.removeItem(draftKey);
      }
    }

    const rawScript = sessionScript || serverScript || "";
    const normalizedScript = normalizeTransformScript(rawScript);
    const hasSavedCode = Boolean(
      rawScript ||
        normalizedScript.trim() ||
        unwrapTransformBody(normalizedScript).trim() ||
        draftScript ||
        activeScript,
    );

    if (hasSavedCode) {
      const scriptToSet = normalizedScript || rawScript;
      setScriptText(scriptToSet);
      setApplyScript(true);
      setScriptNameFromUpload(false);
      setScriptFileMeta({
        fileName: `${tableItem.table.toLowerCase()}.py`,
        size: new Blob([scriptToSet]).size,
      });
    } else {
      setScriptText("");
      setApplyScript(sessionApply ?? false);
      setScriptNameFromUpload(false);
      setScriptFileMeta(null);
    }

    const finalScript = hasSavedCode ? normalizedScript || rawScript : "";
    setBaselineScriptText(finalScript);
    setScriptValidatedText(finalScript);
    skipAutosaveUntilRef.current = Date.now() + 1500;
    setStep(0);
    setValidateResult(null);
    setMarkedScriptText(null);
    setDryRunResult(null);
    setModalExpanded(false);
  }, [
    open,
    tableItem.table,
    transformData?.active,
    transformData?.draft,
    draftKey,
  ]);

  const scriptBytes = new Blob([scriptText]).size;
  const scriptLines = scriptText.split("\n").length;
  const scriptBody = unwrapTransformBody(scriptText);
  const defaultScriptName = `${(tableItem.table || "transform").toLowerCase()}.py`;

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

  const isDirty = useMemo(() => {
    const current = normalizeTransformScript(
      persistedScriptText(applyScript, scriptText),
    );
    const baseline = normalizeTransformScript(baselineScriptText);
    return current !== baseline;
  }, [applyScript, baselineScriptText, scriptText]);

  const scriptBlocksNext =
    applyScript &&
    (scriptBytes > MAX_SCRIPT_BYTES ||
      scriptLines > MAX_SCRIPT_LINES ||
      Boolean(transformRootIssue));

  const scriptSchemaFolds = useMemo((): SchemaFold[] => {
    const tableFields = tableItem.table_fields || {};
    const hasSelectedFields =
      Array.isArray(tableItem.selected_fields) &&
      tableItem.selected_fields.length > 0;

    const fields = hasSelectedFields
      ? (tableItem.selected_fields || []).map((colName) => {
          const directMatch = tableFields[colName];
          const caseInsensitiveKey = Object.keys(tableFields).find(
            (k) => k.toLowerCase() === colName.toLowerCase(),
          );
          const info =
            directMatch ??
            (caseInsensitiveKey ? tableFields[caseInsensitiveKey] : undefined);
          const rawType =
            typeof info === "string" ? info : info?.data_type || "VARCHAR";
          return {
            name: colName,
            dtype: coerceScriptSchemaDtype(rawType),
          };
        })
      : Object.entries(tableFields).map(([colName, info]) => {
          const rawType =
            typeof info === "string" ? info : info?.data_type || "VARCHAR";
          return {
            name: colName,
            dtype: coerceScriptSchemaDtype(rawType),
          };
        });

    return [
      {
        name: "__source_df",
        fields,
      },
      {
        name: "__target_df",
        fields,
      },
    ];
  }, [tableItem.selected_fields, tableItem.table_fields]);

  const scriptExamples = useMemo(() => {
    const tableFields = tableItem.table_fields || {};
    const colNames =
      Array.isArray(tableItem.selected_fields) &&
      tableItem.selected_fields.length > 0
        ? tableItem.selected_fields
        : Object.keys(tableFields);
    return buildScriptExamples(colNames, colNames);
  }, [tableItem.selected_fields, tableItem.table_fields]);

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

  const buildPayload = useCallback(
    (publish = false) => ({
      mapping_json: null,
      mapping_overrides: EMPTY_OVERRIDES,
      script_text: persistedScriptText(applyScript, scriptText),
      entry_point: TRANSFORM_ENTRY_POINT,
      run_order: RUN_ORDER_FILTER_SCRIPT_MAPPING,
      destination_table_name: tableItem.table,
      publish,
    }),
    [applyScript, scriptText, tableItem.table],
  );

  const runValidate = useCallback(async () => {
    try {
      const result = await publishMut.mutateAsync(buildPayload(false));
      setValidateResult(result);
      setMarkedScriptText(persistedScriptText(applyScript, scriptText));
      return result;
    } catch {
      setValidateResult(null);
      setMarkedScriptText(null);
      toaster.error({
        title: "Validation failed",
        description:
          "Check your script syntax, entry point, and backend connectivity.",
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void runValidateRef.current();
          },
        },
      });
      throw new Error("validate failed");
    }
  }, [applyScript, buildPayload, publishMut, scriptText]);

  useEffect(() => {
    runValidateRef.current = runValidate;
  }, [runValidate]);

  const handleScriptSaveAndValidate = async () => {
    if (readOnly) return;
    try {
      const result = await runValidate();
      const current = persistedScriptText(applyScript, scriptText);
      setMarkedScriptText(current);
      if (result.valid) {
        setScriptValidatedText(current);
        toaster.success({ title: "Script validated" });
      } else {
        const first =
          result.errors?.[0]?.message ||
          result.checklist?.find((c) => !c.passed)?.message ||
          "Script validation failed";
        toaster.error({ title: first });
      }
    } catch {
      // toast shown in runValidate
    }
  };

  const handleInsertScriptExample = useCallback((code: string) => {
    setScriptText(wrapTransformBody(code));
    setScriptValidatedText("");
    setDryRunResult(null);
  }, []);

  const handlePickScriptFile = useCallback(async (file: File) => {
    let text = "";
    try {
      text = await file.text();
    } catch {
      toaster.error({ title: "Failed to read file" });
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
    setScriptText(wrapped);
    setScriptFileMeta({ fileName: file.name, size: file.size });
    setScriptNameFromUpload(true);
    setScriptValidatedText("");
    setDryRunResult(null);
  }, []);

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
    setDryRunResult(null);
  }, []);

  const handleApplyScriptChange = useCallback(
    async (checked: boolean) => {
      if (checked) {
        setApplyScript(true);
        if (!scriptTextRef.current.trim()) {
          const seeded = wrapTransformBody("");
          scriptTextRef.current = seeded;
          setScriptText(seeded);
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

  const runDryRun = useCallback(async () => {
    try {
      const result = await dryRunMut.mutateAsync({
        script_text: persistedScriptText(applyScript, scriptText),
        entry_point: TRANSFORM_ENTRY_POINT,
        selected_fields: tableItem.selected_fields ?? [],
        sort_columns: [],
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
          : "Dry run failed. Check Snowflake connectivity and your transform script.";
      toaster.error({
        title: "Dry run failed",
        description: message,
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void runDryRunRef.current();
          },
        },
      });
      throw err;
    }
  }, [applyScript, dryRunMut, scriptText, tableItem.selected_fields]);

  useEffect(() => {
    runDryRunRef.current = runDryRun;
  }, [runDryRun]);

  const flushDraftAutosave = useCallback(async () => {
    if (readOnly || !open) return;
    if (Date.now() < skipAutosaveUntilRef.current) return;
    if (!isDirty) return;
    try {
      setDraftSaveLabel("Saving draft…");
      await draftSaveMut.mutateAsync(buildPayload(false));
      setDraftSaveLabel("Draft saved");
      setBaselineScriptText(persistedScriptText(applyScript, scriptText));
    } catch {
      setDraftSaveLabel("Draft save failed");
    }
  }, [
    applyScript,
    buildPayload,
    draftSaveMut,
    isDirty,
    open,
    readOnly,
    scriptText,
  ]);

  useEffect(() => {
    if (!open || readOnly) return;
    if (Date.now() < skipAutosaveUntilRef.current) return;
    if (!isDirty) return;
    const timer = window.setTimeout(() => {
      void flushDraftAutosave();
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [flushDraftAutosave, isDirty, open, readOnly]);

  useEffect(() => {
    if (!open) return;
    sessionStorage.setItem(
      draftKey,
      JSON.stringify({
        scriptText,
        applyScript,
      }),
    );
  }, [applyScript, draftKey, open, scriptText]);

  const requestClose = async () => {
    if (closeGuard.current) return;
    if (isDirty) {
      const choice = await confirm({
        title: confirmCopy.discard.title,
        description: confirmCopy.discard.description(tableItem.table),
        cancelLabel: confirmCopy.discard.cancelLabel,
        confirmLabel: confirmCopy.discard.confirmLabel,
        icon: "warning",
      });
      if (!choice) return;
    }
    sessionStorage.removeItem(draftKey);
    onClose();
  };

  const handleValidateAndPublish = async () => {
    const tested = Boolean(
      dryRunResult &&
        (dryRunResult.errors?.length ?? 0) === 0 &&
        (dryRunResult.rows_out ?? 0) >= 0,
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
      // Publishing needs table/connection context, so use the scoped source
      // endpoint. The stateless validator is intentionally used only by the
      // editor's validate action and cannot publish a version.
      const result = await publishMut.mutateAsync(buildPayload(true));
      setValidateResult(result);
      setMarkedScriptText(persistedScriptText(applyScript, scriptText));
      if (!result.valid) {
        const first =
          result.errors?.[0]?.message ||
          result.publish_error ||
          "Validation failed";
        toaster.error({ title: first });
        return;
      }
      if (result.published) {
        sessionStorage.removeItem(draftKey);
        setBaselineScriptText(persistedScriptText(applyScript, scriptText));
        setScriptValidatedText(persistedScriptText(applyScript, scriptText));
        toaster.success({
          title: confirmCopy.published(result.published.version),
        });
        queryClient.invalidateQueries({
          queryKey: transformQueryKey(connectionId, tableItem.table),
        });
        queryClient.invalidateQueries({
          queryKey: ["ReverseSchema", connectionId],
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
            void handleValidateAndPublish();
          },
        },
      });
    }
  };

  const handleRemove = async () => {
    const ok = await confirm({
      title: "Remove transform",
      description:
        "Are you sure you want to remove the transformation for this table? Extracted rows will be exported directly without routine processing.",
      cancelLabel: "Keep transform",
      confirmLabel: "Remove transform",
      tone: "danger",
      icon: "danger",
    });
    if (!ok) return;
    try {
      await deactivateMut.mutateAsync();
      sessionStorage.removeItem(draftKey);
      toaster.success({ title: "Transform removed" });
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", connectionId],
      });
      onClose();
    } catch {
      toaster.error({
        title: "Could not remove transform",
        action: {
          label: confirmCopy.retry,
          onClick: () => {
            void handleRemove();
          },
        },
      });
    }
  };

  const outputPreviewRows =
    dryRunResult?.preview_rows ?? dryRunResult?.output_preview ?? [];
  const inputPreviewRows = dryRunResult?.input_preview ?? [];
  const outputColumns =
    dryRunResult?.output_columns ?? dryRunResult?.columns_out ?? [];

  return (
    <>
      <Dialog.Root
        open={open}
        onOpenChange={(details) => {
          if (!details.open) {
            if (confirmOpen) return;
            void requestClose();
          }
        }}
        aria-labelledby="file-export-transform-modal-title"
      >
        <Portal>
          <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
          <Dialog.Positioner>
            <Dialog.Content
              borderRadius="xl"
              maxW={modalExpanded ? "95vw" : "1120px"}
              w="100%"
              h={modalExpanded ? "92vh" : "90vh"}
              maxH={modalExpanded ? "92vh" : "90vh"}
              display="flex"
              flexDirection="column"
              boxShadow="2xl"
              overflow="hidden"
            >
              <Dialog.Header
                bg="gray.50"
                px={5}
                py={2.5}
                borderBottomWidth={1}
                borderColor="gray.200"
                flexShrink={0}
              >
                <Flex align="center" gap={2}>
                  <Text color="gray.800" fontWeight="bold" fontSize="md">
                    Transform
                  </Text>
                  <Badge colorPalette="brand" variant="subtle" size="sm">
                    {tableItem.table}
                  </Badge>
                </Flex>
                <Flex align="center" gap={1.5} ml="auto">
                  <IconButton
                    size="xs"
                    variant="ghost"
                    color="gray.600"
                    _hover={{ bg: "gray.200", color: "gray.800" }}
                    aria-label={
                      modalExpanded
                        ? "Collapse transform dialog"
                        : "Expand transform dialog"
                    }
                    onClick={() => setModalExpanded((value) => !value)}
                  >
                    {modalExpanded ? (
                      <FiMinimize2 size={15} />
                    ) : (
                      <FiMaximize2 size={15} />
                    )}
                  </IconButton>
                  <CloseButton
                    size="sm"
                    aria-label="Close dialog"
                    onClick={() => void requestClose()}
                    color="gray.600"
                    _hover={{ bg: "gray.200", color: "gray.800" }}
                  />
                </Flex>
              </Dialog.Header>

              <Flex
                px={5}
                py={2}
                gap={2}
                flexShrink={0}
                borderBottomWidth={1}
                borderColor="gray.100"
                bg="white"
                align="center"
                flexWrap="wrap"
              >
                {STEPS.map((label, idx) => {
                  const hasIssue = idx === 0 && scriptBlocksNext;
                  const isActive = step === idx;
                  return (
                    <Badge
                      key={label}
                      colorPalette={isActive ? "brand" : "gray"}
                      variant={isActive ? "solid" : "surface"}
                      px={3}
                      py={1}
                      borderRadius="full"
                      fontSize="xs"
                      cursor="pointer"
                      onClick={() => {
                        if (idx === 1 && scriptBlocksNext) return;
                        setStep(idx);
                      }}
                      _hover={!isActive ? { bg: "gray.200" } : undefined}
                      transition="all 0.15s"
                    >
                      {idx + 1}. {label}
                      {hasIssue ? " •" : ""}
                    </Badge>
                  );
                })}
                <Flex ml="auto" gap={2} align="center">
                  {hasServerDraft || isDirty ? (
                    <Badge colorPalette="orange" variant="subtle" size="sm">
                      {confirmCopy.draftBadge}
                    </Badge>
                  ) : hasActive && publishedVersion !== null ? (
                    <Badge colorPalette="green" variant="subtle" size="sm">
                      {confirmCopy.validatedBadge(publishedVersion)}
                    </Badge>
                  ) : (
                    <Badge colorPalette="gray" variant="subtle" size="sm">
                      No validated version
                    </Badge>
                  )}
                  {draftSaveLabel && (hasServerDraft || isDirty) && (
                    <Text fontSize="2xs" color="gray.500">
                      {draftSaveLabel}
                    </Text>
                  )}
                </Flex>
              </Flex>

              <Dialog.Body
                overflowY="auto"
                flex="1"
                px={5}
                py={3}
                display="flex"
                flexDirection="column"
              >
                {step === 0 && (
                  <VStack align="stretch" gap={2.5}>
                    {readOnly && (
                      <Text fontSize="2xs" color="orange.700">
                        You can view this transform routine. Editing requires
                        table edit permission.
                      </Text>
                    )}

                    <Flex
                      align="center"
                      justify="space-between"
                      gap={3}
                      px={3.5}
                      py={2}
                      borderWidth={1}
                      borderColor="gray.200"
                      borderRadius="md"
                      bg="gray.50"
                    >
                      <Flex align="center" gap={2}>
                        <Text
                          fontSize="xs"
                          fontWeight="semibold"
                          color="gray.700"
                        >
                          Apply routine
                        </Text>
                        <Text fontSize="2xs" color="gray.500">
                          Turn on to write or upload a Python routine for
                          Snowflake extract. When off, data exports directly.
                        </Text>
                      </Flex>
                      <Switch.Root
                        checked={applyScript}
                        disabled={readOnly}
                        colorPalette="brand"
                        size="sm"
                        aria-label="Apply routine"
                        onCheckedChange={({ checked }) => {
                          void handleApplyScriptChange(checked);
                        }}
                      >
                        <Switch.HiddenInput />
                        <Switch.Control />
                      </Switch.Root>
                    </Flex>

                    {applyScript ? (
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
                          validating={publishMut.isPending}
                          validation={editorScriptValidation}
                          diagnostics={editorDiagnostics}
                          readOnly={readOnly}
                          compact
                          onChange={(next) => {
                            if (next === scriptText) return;
                            setScriptText(next);
                            setDryRunResult(null);
                          }}
                        />
                      </>
                    ) : (
                      <Box
                        py={12}
                        px={6}
                        borderWidth={1}
                        borderStyle="dashed"
                        borderColor="gray.300"
                        borderRadius="md"
                        bg="gray.50/50"
                        textAlign="center"
                      >
                        <Text
                          fontSize="sm"
                          color="gray.600"
                          mb={1}
                          fontWeight="medium"
                        >
                          Transform routine is disabled
                        </Text>
                        <Text fontSize="xs" color="gray.500">
                          Snowflake extract rows will be written to SharePoint
                          without transformation. Turn on{" "}
                          <strong>Apply routine</strong> above to customize.
                        </Text>
                      </Box>
                    )}
                  </VStack>
                )}

                {step === 1 && (
                  <VStack align="stretch" gap={3} flex="1" minH={0}>
                    <Flex
                      align="center"
                      justify="space-between"
                      gap={3}
                      px={3.5}
                      py={2}
                      borderWidth={1}
                      borderColor="gray.200"
                      borderRadius="md"
                      bg="gray.50"
                      flexWrap="wrap"
                    >
                      <Flex align="center" gap={2} flexWrap="wrap">
                        <Badge size="sm" variant="outline" colorPalette="gray">
                          Snowflake sample: {DEFAULT_DRY_RUN_ROWS} rows
                        </Badge>
                        {dryRunResult && (
                          <>
                            <Badge
                              size="sm"
                              colorPalette="blue"
                              variant="subtle"
                            >
                              In: {dryRunResult.rows_in ?? 0}
                            </Badge>
                            <Badge
                              size="sm"
                              colorPalette="green"
                              variant="subtle"
                            >
                              Out: {dryRunResult.rows_out ?? 0}
                            </Badge>
                            {outputColumns.length > 0 && (
                              <Badge
                                size="sm"
                                colorPalette="purple"
                                variant="subtle"
                              >
                                Cols: {outputColumns.length}
                                {typeof dryRunResult.expected_columns
                                  ?.length === "number"
                                  ? `/${dryRunResult.expected_columns.length}`
                                  : ""}
                              </Badge>
                            )}
                          </>
                        )}
                      </Flex>
                      <Button
                        colorPalette="brand"
                        size="xs"
                        loading={dryRunMut.isPending}
                        disabled={
                          !applyScript ||
                          Boolean(transformRootIssue) ||
                          !scriptBody.trim()
                        }
                        onClick={runDryRun}
                      >
                        Run dry run
                      </Button>
                    </Flex>

                    {!applyScript && (
                      <Text fontSize="xs" color="gray.500">
                        Apply routine is off. Turn on &quot;Apply routine&quot;
                        on the Script step to test a Python transformation.
                      </Text>
                    )}

                    {transformRootIssue && (
                      <Text fontSize="xs" color="red.600">
                        {transformRootIssue.message}
                        {transformRootIssue.line
                          ? ` (line ${transformRootIssue.line})`
                          : ""}
                      </Text>
                    )}

                    {dryRunResult ? (
                      <>
                        {dryRunResult.checklist &&
                          dryRunResult.checklist.length > 0 && (
                            <Flex gap={2} flexWrap="wrap">
                              {dryRunResult.checklist.map((item) => (
                                <Badge
                                  key={item.id}
                                  size="xs"
                                  colorPalette={item.passed ? "green" : "red"}
                                  variant="subtle"
                                >
                                  {item.passed ? "✓" : "✗"} {item.label}
                                </Badge>
                              ))}
                            </Flex>
                          )}

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
                            <Box
                              key={error.message}
                              p={2.5}
                              bg="red.50"
                              borderRadius="md"
                              borderWidth={1}
                              borderColor="red.200"
                            >
                              <Text
                                fontSize="xs"
                                color="red.700"
                                fontWeight="semibold"
                              >
                                {error.message}
                                {line ? ` (line ${line})` : ""}
                              </Text>
                              {error.traceback ? (
                                <Box
                                  as="pre"
                                  mt={1.5}
                                  fontSize="2xs"
                                  whiteSpace="pre-wrap"
                                  fontFamily="mono"
                                  color="red.800"
                                >
                                  {error.traceback}
                                </Box>
                              ) : null}
                            </Box>
                          );
                        })}

                        {inputPreviewRows.length > 0 && (
                          <FileExportPreviewTable
                            title="Input sample (Snowflake extract)"
                            rows={inputPreviewRows}
                            maxH={
                              outputPreviewRows.length > 0 ? "220px" : undefined
                            }
                            flex={outputPreviewRows.length > 0 ? undefined : 1}
                          />
                        )}

                        {outputPreviewRows.length > 0 && (
                          <FileExportPreviewTable
                            title="Target preview (__target_df)"
                            rows={outputPreviewRows}
                            flex={1}
                          />
                        )}

                        {(dryRunResult.rows_out ?? 0) > 0 &&
                          outputPreviewRows.length > 0 &&
                          outputPreviewRows.length < dryRunResult.rows_out && (
                            <Text fontSize="xs" color="gray.500">
                              Preview shows first {outputPreviewRows.length} of{" "}
                              {dryRunResult.rows_out} output rows.
                            </Text>
                          )}
                      </>
                    ) : (
                      <Box
                        py={10}
                        px={6}
                        borderWidth={1}
                        borderStyle="dashed"
                        borderColor="gray.300"
                        borderRadius="md"
                        bg="gray.50/50"
                        textAlign="center"
                      >
                        <Text
                          fontSize="sm"
                          color="gray.600"
                          mb={1}
                          fontWeight="medium"
                        >
                          No dry run executed yet
                        </Text>
                        <Text fontSize="xs" color="gray.500">
                          Click <strong>Run dry run</strong> above to test your
                          transformation routine against sample data from
                          Snowflake.
                        </Text>
                      </Box>
                    )}
                  </VStack>
                )}
              </Dialog.Body>

              <Dialog.Footer
                bg="gray.50"
                px={5}
                py={2.5}
                borderTopWidth={1}
                borderColor="gray.200"
                flexShrink={0}
                justifyContent="space-between"
              >
                <Flex gap={2} align="center">
                  {hasActive && (
                    <Link
                      color="red.500"
                      fontSize="xs"
                      onClick={() => void handleRemove()}
                      cursor="pointer"
                    >
                      Remove transform
                    </Link>
                  )}
                </Flex>
                <Flex gap={2}>
                  {step > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setStep(step - 1)}
                    >
                      Back
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void requestClose()}
                  >
                    Cancel
                  </Button>
                  {step === 0 ? (
                    <Button
                      size="sm"
                      colorPalette="brand"
                      loading={publishMut.isPending}
                      disabled={scriptBlocksNext}
                      onClick={async () => {
                        if (scriptBlocksNext) return;
                        if (applyScript && scriptBody.trim()) {
                          try {
                            await runValidate();
                            setStep(1);
                          } catch {
                            /* toast shown in runValidate */
                          }
                        } else {
                          setStep(1);
                        }
                      }}
                    >
                      Next
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      colorPalette="brand"
                      disabled={readOnly || Boolean(transformRootIssue)}
                      loading={publishMut.isPending}
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

export default FileExportTransformModal;
