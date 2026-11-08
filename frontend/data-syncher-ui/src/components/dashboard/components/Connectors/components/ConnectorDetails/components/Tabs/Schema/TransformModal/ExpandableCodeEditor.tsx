import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Box, Button, Flex, IconButton, Text } from "@chakra-ui/react";

import {
  FiCheck,
  FiCopy,
  FiDownload,
  FiMaximize2,
  FiMinimize2,
} from "react-icons/fi";

import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import {
  MAX_SCRIPT_BYTES,
  MAX_SCRIPT_LINES,
} from "@/constants/transform-limits";

import { confirmCopy } from "./confirmCopy";
import { coerceScriptSchemaDtype } from "./scriptScaffold";
import { python } from "@codemirror/lang-python";
import {
  type Diagnostic,
  forceLinting,
  lintGutter,
  linter,
} from "@codemirror/lint";
import type { Text as DocumentText } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";

export type SchemaFoldField = {
  name: string;
  /** pandas dtype shown in Script schema folds */
  dtype: string;
  /** @deprecated legacy SAP label; coerced to pandas when present */
  typeLabel?: string;
};

export type SchemaFold = {
  name: string;
  fields: SchemaFoldField[];
};

export type EditorExample = {
  id: string;
  label: string;
  description: string;
  code: string;
};

export type EditorValidation = {
  passed: boolean;
  message: string;
  line?: number;
};

/** A problem marked on a source line, the way an IDE shows a diagnostic. */
export type EditorDiagnostic = {
  line?: number;
  column?: number;
  message: string;
  detail?: string;
  severity?: "error" | "warning";
  /** Server results are scrolled into view. Typing-time checks are not. */
  source?: "editor" | "server";
};

function diagnosticFor(
  doc: DocumentText,
  item: EditorDiagnostic,
): Diagnostic | null {
  if (doc.length === 0) return null;
  const reported = item.line && item.line > 0 ? item.line : 1;
  const line = doc.line(Math.min(reported, doc.lines));
  let from = line.from;
  let to = line.to;
  if (item.column && item.column > 0) {
    from = Math.min(line.to, line.from + item.column - 1);
    to = Math.min(line.to, from + 1);
  }
  if (from >= to) to = Math.min(doc.length, from + 1);
  if (from >= to) return null;
  const severity = item.severity === "warning" ? "warning" : "error";
  return {
    from,
    to,
    severity,
    source: "Python",
    message: item.message,
    renderMessage: item.detail
      ? () => {
          const node = document.createElement("div");
          const title = document.createElement("div");
          title.textContent = item.message;
          const pre = document.createElement("pre");
          pre.textContent = item.detail ?? "";
          pre.style.whiteSpace = "pre-wrap";
          pre.style.margin = "6px 0 0";
          pre.style.fontFamily = "monospace";
          node.append(title, pre);
          return node;
        }
      : undefined,
  };
}

interface ExpandableCodeEditorProps {
  value: string;
  onChange: (_value: string) => void;
  readOnly?: boolean;
  exportText?: string;
  lockedHeader?: string;
  lockedFooter?: string;
  schemaFolds?: SchemaFold[];
  examples?: EditorExample[];
  onInsertExample?: (_code: string) => void;
  onValidate?: () => void;
  validateLabel?: string;
  validating?: boolean;
  validateDisabled?: boolean;
  validation?: EditorValidation | null;
  diagnostics?: EditorDiagnostic[];
  /** Script step: editor + expand only. Hide copy and download. */
  compact?: boolean;
  /** Label shown in the editor chrome. Defaults to transform.py. */
  fileName?: string;
}

const EDITOR_BG = "#282c34";
const CHROME_BG = "#21252b";
const STATUS_BG = "#007acc";

const ExpandableCodeEditor = ({
  value,
  onChange,
  readOnly = false,
  exportText,
  lockedHeader,
  lockedFooter,
  schemaFolds = [],
  examples = [],
  onInsertExample,
  onValidate,
  validateLabel = "Validate",
  validating = false,
  validateDisabled = false,
  validation = null,
  diagnostics = [],
  compact = false,
  fileName = "transform.py",
}: ExpandableCodeEditorProps) => {
  const textToExport = exportText ?? value;
  const [expanded, setExpanded] = useState(false);
  const [openFolds, setOpenFolds] = useState<Record<string, boolean>>({});
  const [activeExampleId, setActiveExampleId] = useState<string | null>(null);
  const [cursor, setCursor] = useState({ line: 1, col: 1 });
  const viewRef = useRef<EditorView | null>(null);
  const lineCount = useMemo(
    () => (value ? value.split("\n").length : 1),
    [value],
  );
  const byteSize = useMemo(() => new Blob([value]).size, [value]);
  const activeExample =
    examples.find((example) => example.id === activeExampleId) ?? null;

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(textToExport).then(() => {
      toaster.success({ title: confirmCopy.copied });
    });
  }, [textToExport]);

  const handleDownload = useCallback(() => {
    const blob = new Blob([textToExport], { type: "text/x-python" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "transform.py";
    a.click();
    URL.revokeObjectURL(url);
  }, [textToExport]);

  const editorHeight = expanded ? "calc(100vh - 280px)" : "280px";
  const extensions = useMemo(
    () => [
      python(),
      lintGutter(),
      linter(
        (view) =>
          diagnostics.flatMap((item) => {
            const mark = diagnosticFor(view.state.doc, item);
            return mark ? [mark] : [];
          }),
        { delay: 50 },
      ),
      EditorView.theme({
        ".cm-lintRange-error": {
          backgroundImage: "none",
          textDecoration: "underline wavy #f14c4c",
          textDecorationSkipInk: "none",
        },
        ".cm-lintRange-warning": {
          backgroundImage: "none",
          textDecoration: "underline wavy #cca700",
          textDecorationSkipInk: "none",
        },
      }),
    ],
    [diagnostics],
  );

  const jumpToLine = useCallback((lineNumber?: number) => {
    const view = viewRef.current;
    if (!view) return;
    const reported = lineNumber && lineNumber > 0 ? lineNumber : 1;
    const line = view.state.doc.line(Math.min(reported, view.state.doc.lines));
    view.dispatch({
      selection: { anchor: line.from },
      scrollIntoView: true,
    });
    view.focus();
  }, []);

  const serverDiagnosticKey = diagnostics
    .filter((item) => item.source === "server")
    .map((item) => `${item.line ?? 0}:${item.message}`)
    .join("|");

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    forceLinting(view);
  }, [diagnostics, value]);

  useEffect(() => {
    if (!serverDiagnosticKey) return;
    const first = diagnostics.find(
      (item) => item.source === "server" && item.line,
    );
    jumpToLine(first?.line);
  }, [diagnostics, jumpToLine, serverDiagnosticKey]);

  const shell = (
    <Box
      borderWidth={1}
      borderColor="#181a1f"
      borderRadius="md"
      overflow="hidden"
      bg={EDITOR_BG}
      position={expanded ? "fixed" : "relative"}
      inset={expanded ? 0 : undefined}
      zIndex={expanded ? 1400 : undefined}
      display="flex"
      flexDirection="column"
    >
      <Flex
        align="center"
        gap={2}
        px={2}
        py={1}
        bg={CHROME_BG}
        borderBottomWidth="1px"
        borderColor="whiteAlpha.200"
        flexWrap="wrap"
      >
        <Flex align="center" gap={2} mr={2}>
          <Text fontSize="xs" color="white" fontFamily="mono">
            {fileName}
          </Text>
          <Text
            fontSize="2xs"
            color="#9cdcfe"
            bg="whiteAlpha.100"
            px={1.5}
            py={0.5}
            borderRadius="sm"
          >
            Python
          </Text>
        </Flex>
        {examples.map((example) => (
          <Button
            key={example.id}
            size="xs"
            variant={activeExampleId === example.id ? "solid" : "outline"}
            color="white"
            borderColor="whiteAlpha.400"
            bg={
              activeExampleId === example.id ? "whiteAlpha.300" : "transparent"
            }
            _hover={{ bg: "whiteAlpha.200" }}
            onClick={() =>
              setActiveExampleId((current) =>
                current === example.id ? null : example.id,
              )
            }
          >
            {example.label}
          </Button>
        ))}
        <Flex ml="auto" align="center" gap={1}>
          <Tooltip content={expanded ? "Collapse editor" : "Expand editor"}>
            <IconButton
              aria-label={expanded ? "Collapse" : "Expand"}
              size="xs"
              variant="ghost"
              color="white"
              onClick={() => setExpanded((open) => !open)}
            >
              {expanded ? <FiMinimize2 /> : <FiMaximize2 />}
            </IconButton>
          </Tooltip>
          {!compact && (
            <Tooltip content="Copy">
              <IconButton
                aria-label="Copy"
                size="xs"
                variant="ghost"
                color="white"
                onClick={handleCopy}
              >
                <FiCopy />
              </IconButton>
            </Tooltip>
          )}
          {!compact && (
            <Tooltip content="Download">
              <IconButton
                aria-label="Download"
                size="xs"
                variant="ghost"
                color="white"
                onClick={handleDownload}
              >
                <FiDownload />
              </IconButton>
            </Tooltip>
          )}
          {onValidate && (
            <Button
              size="xs"
              variant="outline"
              color="white"
              borderColor="whiteAlpha.400"
              bg="transparent"
              _hover={{ bg: "whiteAlpha.200" }}
              loading={validating}
              disabled={validateDisabled}
              onClick={onValidate}
            >
              <FiCheck /> {validateLabel}
            </Button>
          )}
        </Flex>
      </Flex>

      {activeExample && (
        <Box
          px={3}
          py={2}
          bg="#1e1e1e"
          borderBottomWidth="1px"
          borderColor="whiteAlpha.200"
        >
          <Text fontSize="xs" color="gray.300" mb={2}>
            {activeExample.description}
          </Text>
          <Box
            as="pre"
            m={0}
            p={2}
            bg={EDITOR_BG}
            color="#abb2bf"
            fontSize="xs"
            fontFamily="mono"
            overflowX="auto"
            whiteSpace="pre"
            aria-readonly="true"
          >
            {activeExample.code}
          </Box>
          <Button
            size="xs"
            mt={2}
            variant="outline"
            color="white"
            borderColor="whiteAlpha.400"
            bg="transparent"
            _hover={{ bg: "whiteAlpha.200" }}
            disabled={readOnly || !onInsertExample}
            onClick={() => onInsertExample?.(activeExample.code)}
          >
            Insert
          </Button>
        </Box>
      )}

      <Box flex={expanded ? 1 : undefined} overflow="auto">
        {schemaFolds.map((fold) => {
          const open = Boolean(openFolds[fold.name]);
          return (
            <Box
              key={fold.name}
              bg={EDITOR_BG}
              borderBottomWidth="1px"
              borderColor="whiteAlpha.100"
              aria-readonly="true"
            >
              <Button
                type="button"
                variant="ghost"
                width="100%"
                justifyContent="flex-start"
                height="auto"
                minH="0"
                px={2}
                py={1}
                gap={2}
                bg="transparent"
                color="#abb2bf"
                fontFamily="mono"
                fontSize="xs"
                fontWeight="normal"
                borderRadius="0"
                _hover={{ bg: "whiteAlpha.100" }}
                aria-expanded={open}
                aria-label={`${open ? "Collapse" : "Expand"} ${fold.name}`}
                onClick={() =>
                  setOpenFolds((current) => ({
                    ...current,
                    [fold.name]: !current[fold.name],
                  }))
                }
              >
                <Text as="span" w="16px" color="#636d83">
                  {open ? "▼" : "▶"}
                </Text>
                <Text as="span">
                  {fold.name} · DataFrame · {fold.fields.length} columns ·
                  pandas dtypes
                </Text>
              </Button>
              {open && (
                <Box maxH="180px" overflowY="auto" pb={1}>
                  {fold.fields.length === 0 ? (
                    <Text
                      pl={8}
                      fontSize="xs"
                      fontFamily="mono"
                      color="#7f848e"
                    >
                      No columns in the mapping JSON
                    </Text>
                  ) : (
                    fold.fields.map((field) => (
                      <Flex
                        key={field.name}
                        pl={8}
                        pr={3}
                        gap={6}
                        fontSize="xs"
                        fontFamily="mono"
                        color="#abb2bf"
                      >
                        <Text minW="140px">{field.name}</Text>
                        <Text color="#7f848e">
                          {coerceScriptSchemaDtype(
                            field.dtype || field.typeLabel || "",
                          ) || "—"}
                        </Text>
                      </Flex>
                    ))
                  )}
                </Box>
              )}
            </Box>
          );
        })}

        {lockedHeader && (
          <Box
            as="pre"
            m={0}
            px={3}
            py={2}
            bg={EDITOR_BG}
            color="#7f848e"
            fontSize="13px"
            fontFamily="mono"
            overflowX="auto"
            whiteSpace="pre"
            aria-readonly="true"
          >
            {lockedHeader}
          </Box>
        )}
        <CodeMirror
          value={value}
          height={editorHeight}
          theme={oneDark}
          extensions={extensions}
          editable={!readOnly}
          onChange={onChange}
          onCreateEditor={(view) => {
            viewRef.current = view;
            forceLinting(view);
          }}
          onUpdate={(update) => {
            const pos = update.state.selection.main.head;
            const line = update.state.doc.lineAt(pos);
            const next = { line: line.number, col: pos - line.from + 1 };
            setCursor((current) =>
              current.line === next.line && current.col === next.col
                ? current
                : next,
            );
          }}
          basicSetup={{ lineNumbers: true, foldGutter: true }}
        />
        {lockedFooter && (
          <Box
            as="pre"
            m={0}
            px={3}
            py={2}
            bg={EDITOR_BG}
            color="#7f848e"
            fontSize="13px"
            fontFamily="mono"
            overflowX="auto"
            whiteSpace="pre"
            aria-readonly="true"
          >
            {lockedFooter}
          </Box>
        )}
      </Box>

      {diagnostics.length > 0 && (
        <Box
          bg="#1e1e1e"
          borderTopWidth="1px"
          borderColor="whiteAlpha.200"
          maxH="180px"
          overflowY="auto"
          flexShrink={0}
        >
          {diagnostics.map((item, index) => (
            <Button
              key={`${item.line ?? 0}-${item.message}-${index}`}
              type="button"
              variant="ghost"
              width="100%"
              height="auto"
              minH="0"
              flexDirection="column"
              alignItems="flex-start"
              justifyContent="flex-start"
              textAlign="left"
              whiteSpace="normal"
              borderRadius="0"
              px={3}
              py={2}
              bg="transparent"
              borderBottomWidth="1px"
              borderColor="whiteAlpha.100"
              _hover={{ bg: "whiteAlpha.100" }}
              onClick={() => jumpToLine(item.line)}
            >
              <Text
                fontSize="xs"
                fontFamily="mono"
                color={item.severity === "warning" ? "#cca700" : "#f48771"}
              >
                {item.message}
                {item.line ? `    Ln ${item.line}` : ""}
                {item.column ? `, Col ${item.column}` : ""}
              </Text>
              {item.detail && item.detail !== item.message && (
                <Box
                  as="pre"
                  m={0}
                  mt={1}
                  whiteSpace="pre-wrap"
                  fontSize="2xs"
                  fontFamily="mono"
                  color="#d4d4d4"
                >
                  {item.detail}
                </Box>
              )}
            </Button>
          ))}
        </Box>
      )}

      <Flex
        align="center"
        gap={4}
        px={3}
        py={1}
        bg={STATUS_BG}
        color="white"
        fontSize="xs"
        flexShrink={0}
      >
        <Text>
          Ln {cursor.line}, Col {cursor.col}
        </Text>
        <Text>
          {lineCount} lines · {(byteSize / 1024).toFixed(1)} KB /{" "}
          {MAX_SCRIPT_BYTES / 1024} KB · max {MAX_SCRIPT_LINES} lines
        </Text>
        <Text ml="auto">Python</Text>
        {diagnostics.length > 0 ? (
          <Text color="#ffd2d2">
            {diagnostics.length}{" "}
            {diagnostics.length === 1 ? "problem" : "problems"}
          </Text>
        ) : (
          validation && (
            <Text color={validation.passed ? "white" : "#ffd2d2"}>
              {validation.message}
            </Text>
          )
        )}
      </Flex>
    </Box>
  );

  return shell;
};

export default ExpandableCodeEditor;
