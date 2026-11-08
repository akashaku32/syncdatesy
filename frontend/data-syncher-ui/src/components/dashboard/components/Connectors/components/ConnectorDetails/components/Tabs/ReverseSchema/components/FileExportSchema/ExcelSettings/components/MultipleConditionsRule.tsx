import { useEffect, useMemo, useRef, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  Flex,
  HStack,
  IconButton,
  Input,
  NativeSelect,
  Text,
  VStack,
} from "@chakra-ui/react";

import { IoMdAdd, IoMdClose, IoMdFolderOpen } from "react-icons/io";
import { MdEdit, MdFilterList, MdInfoOutline, MdSearch } from "react-icons/md";

import { Tooltip } from "@/components/ui/tooltip";
import { type ExcelConditionItem } from "@/types/connectors";

import { getColumnDataType, getValidationError } from "../helpers";
import {
  type ParseResult,
  parseHumanCondition,
} from "../helpers/conditionParser";
import {
  type ConditionRuleGroup,
  type ConditionRuleItem,
  type ConditionRuleNode,
  addRuleToGroup,
  cloneTree,
  conditionsToTree,
  createRule,
  deleteNode,
  findCommonParent,
  findNodeAndParent,
  groupSelectedNodes,
  nodeToHumanSummary,
  toggleGroupConjunction,
  treeToConditions,
  ungroupNode,
} from "../helpers/groupModelUtils";

interface MultipleConditionsRuleProps {
  conditions: ExcelConditionItem[] | undefined;
  targetColumns: string[] | undefined;
  highlightScope?:
    | "cell"
    | "entire_row"
    | "entire_column"
    | "selected_columns"
    | string;
  colNames: string[];
  tableFields?: Record<string, string>;
  onUpdateConditions: (_newConditions: ExcelConditionItem[]) => void;
  onUpdateTargetColumns: (_columns: string[]) => void;
  onUpdateHighlightScope: (
    _scope:
      | "cell"
      | "entire_row"
      | "entire_column"
      | "selected_columns"
      | string,
  ) => void;
}

const MULTIPLE_CONDITION_OPERATORS = [
  { value: "equal", label: "= Equal to" },
  { value: "notEqual", label: "!= Not equal to" },
  { value: "greaterThan", label: "> Greater than" },
  { value: "lessThan", label: "< Less than" },
  { value: "greaterThanOrEqual", label: ">= Greater or equal" },
  { value: "lessThanOrEqual", label: "<= Less or equal" },
  { value: "contains", label: "Contains text" },
  { value: "notContains", label: "Does not contain" },
  { value: "beginsWith", label: "Begins with" },
  { value: "notBeginsWith", label: "Does not begin with" },
  { value: "endsWith", label: "Ends with" },
  { value: "notEndsWith", label: "Does not end with" },
  { value: "between", label: "Between" },
  { value: "notBetween", label: "Not between" },
  { value: "isEmpty", label: "Is empty" },
  { value: "isNotEmpty", label: "Is not empty" },
];

const BOOLEAN_OPERATORS = [
  { value: "equal", label: "= Equal to" },
  { value: "notEqual", label: "!= Not equal to" },
  { value: "isEmpty", label: "Is empty" },
  { value: "isNotEmpty", label: "Is not empty" },
];

const TEXT_OPERATORS = [
  { value: "equal", label: "= Equal to" },
  { value: "notEqual", label: "!= Not equal to" },
  { value: "contains", label: "Contains text" },
  { value: "notContains", label: "Does not contain" },
  { value: "beginsWith", label: "Begins with" },
  { value: "notBeginsWith", label: "Does not begin with" },
  { value: "endsWith", label: "Ends with" },
  { value: "notEndsWith", label: "Does not end with" },
  { value: "isEmpty", label: "Is empty" },
  { value: "isNotEmpty", label: "Is not empty" },
];

const NUMERIC_OPERATORS = [
  { value: "equal", label: "= Equal to" },
  { value: "notEqual", label: "!= Not equal to" },
  { value: "greaterThan", label: "> Greater than" },
  { value: "lessThan", label: "< Less than" },
  { value: "greaterThanOrEqual", label: ">= Greater or equal" },
  { value: "lessThanOrEqual", label: "<= Less or equal" },
  { value: "between", label: "Between" },
  { value: "notBetween", label: "Not between" },
  { value: "isEmpty", label: "Is empty" },
  { value: "isNotEmpty", label: "Is not empty" },
];

const DATETIME_OPERATORS = [
  { value: "equal", label: "= Equal to" },
  { value: "notEqual", label: "!= Not equal to" },
  { value: "greaterThan", label: "> After" },
  { value: "lessThan", label: "< Before" },
  { value: "greaterThanOrEqual", label: ">= On or after" },
  { value: "lessThanOrEqual", label: "<= On or before" },
  { value: "between", label: "Between" },
  { value: "notBetween", label: "Not between" },
  { value: "isEmpty", label: "Is empty" },
  { value: "isNotEmpty", label: "Is not empty" },
];

const getOperatorsForColumn = (
  rawType: string | undefined,
): Array<{ value: string; label: string }> => {
  if (!rawType) return MULTIPLE_CONDITION_OPERATORS;
  const dataType = getColumnDataType(rawType);
  switch (dataType) {
    case "boolean":
      return BOOLEAN_OPERATORS;
    case "text":
      return TEXT_OPERATORS;
    case "numeric":
      return NUMERIC_OPERATORS;
    case "datetime":
      return DATETIME_OPERATORS;
    default:
      return MULTIPLE_CONDITION_OPERATORS;
  }
};

// Searchable Field Selector Component
function SearchableFieldSelect({
  value,
  colNames = [],
  onChange,
}: {
  value: string;
  colNames: string[];
  onChange: (_col: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const filtered = (colNames || []).filter((c) =>
    c.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <Box position="relative" ref={popoverRef} flex="1.3" minWidth="135px">
      <Button
        type="button"
        size="xs"
        variant="outline"
        width="100%"
        justifyContent="space-between"
        onClick={() => setIsOpen(!isOpen)}
        bg="white"
        h="28px"
        px={2.5}
        fontSize="xs"
        fontWeight="normal"
        borderColor="gray.200"
        _hover={{ borderColor: "gray.300", bg: "gray.50" }}
      >
        <Text fontSize="xs" color={value ? "gray.800" : "gray.400"} truncate>
          {value || "-- Select Field --"}
        </Text>
        <Text fontSize="9px" color="gray.400" ml={1}>
          {isOpen ? "▲" : "▼"}
        </Text>
      </Button>

      {isOpen && (
        <Box
          position="absolute"
          bottom="calc(100% + 4px)"
          left="0"
          zIndex={1000}
          bg="white"
          shadow="xl"
          border="1px solid"
          borderColor="gray.200"
          borderRadius="md"
          p={2}
          w="100%"
          minW="210px"
          maxW="290px"
        >
          <HStack gap={1} mb={1.5}>
            <Box color="gray.400" fontSize="13px" pl={0.5}>
              <MdSearch />
            </Box>
            <Input
              size="xs"
              placeholder="Search fields..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              h="26px"
              fontSize="xs"
              autoFocus
            />
          </HStack>
          <Box
            display="flex"
            flexDirection="column"
            gap="2px"
            maxH="175px"
            overflowY="auto"
            pr={0.5}
          >
            <Box
              minH="29px"
              px={2.5}
              py={1}
              display="flex"
              alignItems="center"
              borderRadius="sm"
              cursor="pointer"
              _hover={{ bg: "gray.100" }}
              onMouseDown={(e) => {
                e.preventDefault();
                onChange("");
                setIsOpen(false);
                setSearch("");
              }}
              onClick={() => {
                onChange("");
                setIsOpen(false);
                setSearch("");
              }}
            >
              <Text
                as="span"
                m={0}
                p={0}
                fontSize="xs"
                color="gray.400"
                fontStyle="italic"
                lineHeight="normal"
              >
                -- Select Field --
              </Text>
            </Box>
            {filtered.map((c) => (
              <Box
                key={c}
                minH="29px"
                px={2.5}
                py={1}
                display="flex"
                alignItems="center"
                borderRadius="sm"
                cursor="pointer"
                bg={c === value ? "brand.50" : "transparent"}
                _hover={{ bg: c === value ? "brand.100" : "gray.50" }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onChange(c);
                  setIsOpen(false);
                  setSearch("");
                }}
                onClick={() => {
                  onChange(c);
                  setIsOpen(false);
                  setSearch("");
                }}
              >
                <Text
                  as="span"
                  m={0}
                  p={0}
                  fontSize="xs"
                  fontWeight={c === value ? "semibold" : "normal"}
                  color={c === value ? "brand.700" : "gray.800"}
                  truncate
                  lineHeight="normal"
                >
                  {c}
                </Text>
              </Box>
            ))}
            {filtered.length === 0 && (
              <Box px={2} py={2.5} textAlign="center">
                <Text as="span" m={0} p={0} fontSize="2xs" color="gray.400">
                  {(colNames || []).length === 0
                    ? "No columns available"
                    : "No matching fields"}
                </Text>
              </Box>
            )}
          </Box>
        </Box>
      )}
    </Box>
  );
}

interface Model2ConditionRowProps {
  rule: ConditionRuleItem;
  isSelectionMode: boolean;
  isSelected: boolean;
  isSelectDisabled: boolean;
  disabledReason?: string;
  onToggleSelect: (_id: string) => void;
  colNames: string[];
  tableFields?: Record<string, string>;
  onChange: (_patch: Partial<ConditionRuleItem>) => void;
  onDelete: () => void;
  showDelete: boolean;
}

function Model2ConditionRow({
  rule,
  isSelectionMode,
  isSelected,
  isSelectDisabled,
  disabledReason,
  onToggleSelect,
  colNames,
  tableFields,
  onChange,
  onDelete,
  showDelete,
}: Model2ConditionRowProps) {
  const colRawType = rule.column ? tableFields?.[rule.column] : undefined;
  const colDataType = getColumnDataType(colRawType);
  const availableOperators = getOperatorsForColumn(colRawType);

  const isBetween =
    rule.operator === "between" || rule.operator === "notBetween";
  const isEmptyOrNot = ["isEmpty", "isNotEmpty"].includes(rule.operator);

  return (
    <Flex
      align="center"
      gap={1.5}
      p={1.5}
      bg={isSelected ? "brand.50/40" : "white"}
      borderRadius="md"
      border="1px solid"
      borderColor={isSelected ? "brand.300" : "gray.200"}
      _hover={{ borderColor: isSelected ? "brand.400" : "gray.300" }}
      transition="border-color 0.15s, background-color 0.15s"
      width="100%"
    >
      {/* Checkbox (shown only in selection mode) */}
      {isSelectionMode && (
        <Flex
          align="center"
          justify="center"
          w="26px"
          minW="26px"
          h="28px"
          flexShrink={0}
          title={isSelectDisabled ? disabledReason : undefined}
        >
          <Checkbox.Root
            size="sm"
            colorPalette="brand"
            checked={isSelected}
            disabled={isSelectDisabled}
            onCheckedChange={() => onToggleSelect(rule.id)}
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control
              cursor={isSelectDisabled ? "not-allowed" : "pointer"}
            />
          </Checkbox.Root>
        </Flex>
      )}

      {/* Searchable Field Selector */}
      <SearchableFieldSelect
        value={rule.column}
        colNames={colNames}
        onChange={(selectedCol) => {
          const nextRawType = selectedCol
            ? tableFields?.[selectedCol]
            : undefined;
          const nextDataType = getColumnDataType(nextRawType);
          const allowedOps = getOperatorsForColumn(nextRawType);
          const isCurrentOpAllowed = allowedOps.some(
            (op) => op.value === rule.operator,
          );
          const nextOp = isCurrentOpAllowed
            ? rule.operator
            : allowedOps[0]?.value || "equal";

          let nextVal = rule.value;
          if (
            nextDataType === "boolean" &&
            nextVal !== "TRUE" &&
            nextVal !== "FALSE"
          ) {
            nextVal = "TRUE";
          }

          onChange({
            column: selectedCol,
            operator: nextOp,
            value: nextVal,
          });
        }}
      />

      {/* Operator selector */}
      <NativeSelect.Root size="xs" width="135px" minWidth="120px">
        <NativeSelect.Field
          bg="white"
          fontSize="xs"
          h="28px"
          value={rule.operator}
          onChange={(e) => onChange({ operator: e.target.value })}
        >
          {availableOperators.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </NativeSelect.Field>
        <NativeSelect.Indicator />
      </NativeSelect.Root>

      {/* Value Input(s) */}
      {isEmptyOrNot ? (
        <Box
          flex="1"
          minWidth="70px"
          h="28px"
          bg="gray.50"
          borderRadius="xs"
          display="flex"
          alignItems="center"
          px={2}
        >
          <Text fontSize="xs" color="gray.400" fontStyle="italic">
            (no value)
          </Text>
        </Box>
      ) : colDataType === "boolean" ? (
        <NativeSelect.Root size="xs" flex="1" minWidth="80px">
          <NativeSelect.Field
            bg="white"
            fontSize="xs"
            h="28px"
            value={rule.value || "TRUE"}
            onChange={(e) => onChange({ value: e.target.value })}
          >
            <option value="TRUE">True (TRUE)</option>
            <option value="FALSE">False (FALSE)</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
      ) : isBetween ? (
        <HStack gap={1} flex="1" minWidth="90px">
          {(() => {
            const isVal1Empty = !rule.value || rule.value.trim() === "";
            const val1Err = !isVal1Empty
              ? getValidationError(rule.value, colDataType)
              : null;
            const isVal1Invalid = isVal1Empty || !!val1Err;
            return (
              <Input
                size="xs"
                h="28px"
                fontSize="xs"
                placeholder="Min"
                type={colDataType === "numeric" ? "number" : "text"}
                step={colDataType === "numeric" ? "any" : undefined}
                value={rule.value ?? ""}
                onChange={(e) => onChange({ value: e.target.value })}
                borderColor={isVal1Invalid ? "red.400" : undefined}
                _hover={{
                  borderColor: isVal1Invalid ? "red.500" : "gray.300",
                }}
                _focus={{
                  borderColor: isVal1Invalid ? "red.500" : "brand.500",
                  boxShadow: isVal1Invalid
                    ? "0 0 0 1px var(--chakra-colors-red-500)"
                    : undefined,
                }}
                title={
                  val1Err ||
                  (isVal1Empty ? "Minimum value is required" : undefined)
                }
              />
            );
          })()}
          {(() => {
            const isVal2Empty = !rule.value2 || rule.value2.trim() === "";
            const val2Err = !isVal2Empty
              ? getValidationError(rule.value2, colDataType)
              : null;
            const isVal2Invalid = isVal2Empty || !!val2Err;
            return (
              <Input
                size="xs"
                h="28px"
                fontSize="xs"
                placeholder="Max"
                type={colDataType === "numeric" ? "number" : "text"}
                step={colDataType === "numeric" ? "any" : undefined}
                value={rule.value2 ?? ""}
                onChange={(e) => onChange({ value2: e.target.value })}
                borderColor={isVal2Invalid ? "red.400" : undefined}
                _hover={{
                  borderColor: isVal2Invalid ? "red.500" : "gray.300",
                }}
                _focus={{
                  borderColor: isVal2Invalid ? "red.500" : "brand.500",
                  boxShadow: isVal2Invalid
                    ? "0 0 0 1px var(--chakra-colors-red-500)"
                    : undefined,
                }}
                title={
                  val2Err ||
                  (isVal2Empty ? "Maximum value is required" : undefined)
                }
              />
            );
          })()}
        </HStack>
      ) : (
        (() => {
          const isValEmpty = !rule.value || rule.value.trim() === "";
          const valErr = !isValEmpty
            ? getValidationError(rule.value, colDataType)
            : null;
          const isInvalid = isValEmpty || !!valErr;
          return (
            <Input
              size="xs"
              flex="1"
              minWidth="70px"
              h="28px"
              fontSize="xs"
              placeholder={
                colDataType === "numeric"
                  ? "Number"
                  : colDataType === "datetime"
                    ? "YYYY-MM-DD"
                    : "Value"
              }
              type={colDataType === "numeric" ? "number" : "text"}
              step={colDataType === "numeric" ? "any" : undefined}
              value={rule.value ?? ""}
              onChange={(e) => onChange({ value: e.target.value })}
              borderColor={isInvalid ? "red.400" : undefined}
              _hover={{
                borderColor: isInvalid ? "red.500" : "gray.300",
              }}
              _focus={{
                borderColor: isInvalid ? "red.500" : "brand.500",
                boxShadow: isInvalid
                  ? "0 0 0 1px var(--chakra-colors-red-500)"
                  : undefined,
              }}
              title={valErr || (isValEmpty ? "Value is required" : undefined)}
            />
          );
        })()
      )}

      {/* Delete condition button */}
      <IconButton
        aria-label="Remove condition"
        size="xs"
        variant="ghost"
        colorPalette="red"
        onClick={onDelete}
        disabled={!showDelete}
        h="22px"
        w="22px"
        minW="22px"
        title="Remove condition"
      >
        <IoMdClose fontSize="14px" />
      </IconButton>
    </Flex>
  );
}

export default function MultipleConditionsRule({
  conditions = [],
  targetColumns = [],
  highlightScope = "entire_row",
  colNames,
  tableFields = {},
  onUpdateConditions,
  onUpdateTargetColumns,
  onUpdateHighlightScope,
}: MultipleConditionsRuleProps) {
  const [isColumnDropdownOpen, setIsColumnDropdownOpen] = useState(false);
  const [colSearchQuery, setColSearchQuery] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Model 2 Select & Group Tree State with focus stability guard
  const [tree, setTree] = useState<ConditionRuleGroup>(() =>
    conditionsToTree(conditions),
  );
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const isInternalEditRef = useRef(false);

  // Editable text preview expression state
  const [isEditingText, setIsEditingText] = useState(false);
  const [editText, setEditText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [parsedTree, setParsedTree] = useState<ConditionRuleGroup | null>(null);
  const [parseResult, setParseResult] = useState<ParseResult | null>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  // Sync external condition changes only when structure actually changes externally
  useEffect(() => {
    if (isInternalEditRef.current) {
      isInternalEditRef.current = false;
      return;
    }
    const currentSerialized = JSON.stringify(treeToConditions(tree));
    const nextSerialized = JSON.stringify(conditions || []);
    if (currentSerialized !== nextSerialized) {
      setTree(conditionsToTree(conditions));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conditions]);

  // Click outside to close chosen columns dropdown
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setIsColumnDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleRuleChange = (
    ruleId: string,
    patch: Partial<ConditionRuleItem>,
  ) => {
    isInternalEditRef.current = true;
    const nextTree = cloneTree(tree);
    const loc = findNodeAndParent(nextTree, ruleId);
    if (loc && loc.node.type === "rule") {
      Object.assign(loc.node, patch);
      setTree(nextTree);
      onUpdateConditions(treeToConditions(nextTree));
    }
  };

  const handleToggleSelect = (ruleId: string) => {
    const next = new Set(selectedIds);
    if (next.has(ruleId)) {
      next.delete(ruleId);
    } else {
      next.add(ruleId);
    }
    setSelectedIds(next);
  };

  const firstSelectedId = Array.from(selectedIds)[0];
  const firstSelectedParentId = useMemo(() => {
    if (!firstSelectedId) return null;
    const loc = findNodeAndParent(tree, firstSelectedId);
    return loc?.parent?.id ?? null;
  }, [tree, firstSelectedId]);

  const selectionValidation = useMemo(
    () => findCommonParent(tree, Array.from(selectedIds)),
    [tree, selectedIds],
  );

  const handleGroupSelected = () => {
    if (!selectionValidation.valid) return;
    isInternalEditRef.current = true;
    const nextTree = groupSelectedNodes(tree, Array.from(selectedIds));
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
    setIsSelectionMode(false);
    setSelectedIds(new Set());
  };

  const handleUngroup = (groupId: string) => {
    isInternalEditRef.current = true;
    const nextTree = ungroupNode(tree, groupId);
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
  };

  const handleToggleConjunction = (groupId: string) => {
    isInternalEditRef.current = true;
    const nextTree = toggleGroupConjunction(tree, groupId);
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
  };

  const handleAddCondition = () => {
    isInternalEditRef.current = true;
    const nextTree = cloneTree(tree);
    nextTree.children.push(createRule());
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
  };

  const handleAddRuleToGroup = (groupId: string) => {
    isInternalEditRef.current = true;
    const nextTree = addRuleToGroup(tree, groupId);
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
  };

  const handleDeleteNode = (nodeId: string) => {
    isInternalEditRef.current = true;
    const nextTree = deleteNode(tree, nodeId);
    setTree(nextTree);
    onUpdateConditions(treeToConditions(nextTree));
    if (selectedIds.has(nodeId)) {
      const next = new Set(selectedIds);
      next.delete(nodeId);
      setSelectedIds(next);
    }
  };

  const handleReset = () => {
    isInternalEditRef.current = true;
    const blankTree = conditionsToTree([]);
    setTree(blankTree);
    onUpdateConditions(treeToConditions(blankTree));
    setIsSelectionMode(false);
    setSelectedIds(new Set());
  };

  const handleToggleColumn = (col: string) => {
    const current = new Set(targetColumns);
    if (current.has(col)) {
      current.delete(col);
    } else {
      current.add(col);
    }
    onUpdateTargetColumns(Array.from(current));
  };

  const handleSelectAllColumns = () => {
    onUpdateTargetColumns([...colNames]);
  };

  const handleClearColumns = () => {
    onUpdateTargetColumns([]);
  };

  const filteredCols = colNames.filter((col) =>
    col.toLowerCase().includes(colSearchQuery.toLowerCase()),
  );

  const summary = useMemo(
    () => nodeToHumanSummary(tree, tableFields, true),
    [tree, tableFields],
  );

  const handleStartTextEdit = () => {
    setEditText(summary);
    setParseError(null);
    const result = parseHumanCondition(summary, colNames, tableFields);
    setParseResult(result);
    setParsedTree(result.tree);
    setIsEditingText(true);
  };

  const handleTextChange = (newVal: string) => {
    setEditText(newVal);
    if (!newVal.trim()) {
      setParseError("Condition expression cannot be empty");
      setParsedTree(null);
      setParseResult(null);
      return;
    }
    const result = parseHumanCondition(newVal, colNames, tableFields);
    setParseResult(result);
    setParseError(result.error);
    setParsedTree(result.tree);
  };

  const handleSelectSuggestedColumn = (colName: string) => {
    let nextText = editText;
    let newCursorPos = 0;

    if (parseResult?.fieldRange) {
      const { start, end } = parseResult.fieldRange;
      const trailing = editText.slice(end);
      const needsSpace = trailing.length === 0 || !/^[\s=!<>]/.test(trailing);
      const inserted = needsSpace ? `${colName} ` : colName;
      nextText = editText.slice(0, start) + inserted + trailing;
      newCursorPos = start + inserted.length;
    } else if (parseResult?.unknownField) {
      const idx = editText.lastIndexOf(parseResult.unknownField);
      if (idx !== -1) {
        const trailing = editText.slice(idx + parseResult.unknownField.length);
        const needsSpace = trailing.length === 0 || !/^[\s=!<>]/.test(trailing);
        const inserted = needsSpace ? `${colName} ` : colName;
        nextText = editText.slice(0, idx) + inserted + trailing;
        newCursorPos = idx + inserted.length;
      } else {
        nextText = `${editText} ${colName} `.trim();
        newCursorPos = nextText.length;
      }
    } else {
      const inputEl = editInputRef.current;
      if (inputEl) {
        const start = inputEl.selectionStart ?? editText.length;
        const end = inputEl.selectionEnd ?? editText.length;
        const trailing = editText.slice(end);
        const needsSpace = trailing.length === 0 || !/^[\s=!<>]/.test(trailing);
        const inserted = needsSpace ? `${colName} ` : colName;
        nextText = editText.slice(0, start) + inserted + trailing;
        newCursorPos = start + inserted.length;
      } else {
        nextText = `${editText} ${colName} `.trim();
        newCursorPos = nextText.length;
      }
    }

    handleTextChange(nextText);

    setTimeout(() => {
      if (editInputRef.current) {
        editInputRef.current.focus();
        editInputRef.current.setSelectionRange(newCursorPos, newCursorPos);
      }
    }, 0);
  };

  const displaySuggestions = useMemo(() => {
    // 1. If an unknown field is detected and we have suggestions for it:
    if (
      parseResult?.unknownField &&
      parseResult?.suggestedColumns &&
      parseResult.suggestedColumns.length > 0
    ) {
      return parseResult.suggestedColumns.slice(0, 8);
    }

    // 2. If the user is actively typing a word/token at the end of the input:
    const trimmed = editText.trim();
    if (!trimmed) {
      return []; // Clean state: NO suggestions box when empty
    }

    const lastWordMatch = trimmed.match(/[a-zA-Z0-9_$.%-]+$/);
    if (lastWordMatch) {
      const lastWord = lastWordMatch[0].toLowerCase();
      // Don't suggest if the word is already an exact column match or purely numeric/keyword
      const isExactCol = colNames.some((c) => c.toLowerCase() === lastWord);
      const isKeyword = [
        "and",
        "or",
        "true",
        "false",
        "between",
        "empty",
        "null",
      ].includes(lastWord);
      const isNum = !isNaN(Number(lastWord));

      if (!isExactCol && !isKeyword && !isNum && lastWord.length >= 1) {
        const matched = colNames
          .filter((c) => c.toLowerCase().includes(lastWord))
          .sort((a, b) => {
            const aStarts = a.toLowerCase().startsWith(lastWord);
            const bStarts = b.toLowerCase().startsWith(lastWord);
            if (aStarts && !bStarts) return -1;
            if (!aStarts && bStarts) return 1;
            return a.localeCompare(b);
          });
        if (matched.length > 0) {
          return matched.slice(0, 8);
        }
      }
    }

    return []; // Never show all columns as a generic fallback
  }, [parseResult, editText, colNames]);

  const handleApplyTextEdit = () => {
    if (!parsedTree || parseError) return;
    isInternalEditRef.current = true;
    setTree(parsedTree);
    onUpdateConditions(treeToConditions(parsedTree));
    setIsEditingText(false);
    setParseError(null);
    setParseResult(null);
  };

  const handleCancelTextEdit = () => {
    setIsEditingText(false);
    setParseError(null);
    setParsedTree(null);
    setParseResult(null);
  };

  const isScopeSelectedColumns = highlightScope === "selected_columns";

  let totalRules = 0;
  const countRules = (group: ConditionRuleGroup) => {
    for (const child of group.children) {
      if (child.type === "rule") totalRules++;
      else countRules(child);
    }
  };
  countRules(tree);

  const renderNodeList = (
    nodes: ConditionRuleNode[],
    parentGroup: ConditionRuleGroup,
    depth: number,
  ): React.ReactNode => {
    return (
      <Box display="flex" flexDirection="column" gap={1.5} width="100%">
        {nodes.map((node, idx) => {
          const isFirstChild = idx === 0;

          return (
            <Box
              key={node.id}
              display="flex"
              flexDirection="column"
              gap={1.5}
              width="100%"
            >
              {/* Inter-row conjunction divider between siblings */}
              {!isFirstChild && (
                <HStack
                  gap={2}
                  my={0.5}
                  align="center"
                  pl={isSelectionMode ? "32px" : "8px"}
                >
                  {parentGroup.id === "root" ? (
                    <Button
                      type="button"
                      size="2xs"
                      variant="outline"
                      colorPalette={
                        parentGroup.conjunction === "or" ? "purple" : "blue"
                      }
                      bg={
                        parentGroup.conjunction === "or"
                          ? "purple.50"
                          : "blue.50"
                      }
                      borderColor={
                        parentGroup.conjunction === "or"
                          ? "purple.300"
                          : "blue.300"
                      }
                      color={
                        parentGroup.conjunction === "or"
                          ? "purple.700"
                          : "blue.700"
                      }
                      h="22px"
                      px={2}
                      fontSize="10px"
                      fontWeight="bold"
                      borderRadius="full"
                      onClick={() => handleToggleConjunction(parentGroup.id)}
                      title={`Click to switch between AND and OR (currently ${parentGroup.conjunction.toUpperCase()})`}
                    >
                      {parentGroup.conjunction.toUpperCase()} ▾
                    </Button>
                  ) : (
                    <Box
                      px={2}
                      py={0.5}
                      fontSize="10px"
                      fontWeight="bold"
                      borderRadius="full"
                      bg={
                        parentGroup.conjunction === "or"
                          ? "purple.50"
                          : "blue.50"
                      }
                      border="1px solid"
                      borderColor={
                        parentGroup.conjunction === "or"
                          ? "purple.200"
                          : "blue.200"
                      }
                      color={
                        parentGroup.conjunction === "or"
                          ? "purple.700"
                          : "blue.700"
                      }
                    >
                      {parentGroup.conjunction.toUpperCase()}
                    </Box>
                  )}
                  <Box
                    flex="1"
                    borderBottom="1px dashed"
                    borderColor="gray.200"
                  />
                </HStack>
              )}

              {/* Render rule item or nested group */}
              {node.type === "rule" ? (
                <Model2ConditionRow
                  rule={node}
                  isSelectionMode={isSelectionMode}
                  isSelected={selectedIds.has(node.id)}
                  isSelectDisabled={
                    firstSelectedParentId !== null &&
                    firstSelectedParentId !== parentGroup.id
                  }
                  disabledReason="Can only group items at the same level"
                  onToggleSelect={handleToggleSelect}
                  colNames={colNames}
                  tableFields={tableFields}
                  onChange={(patch) => handleRuleChange(node.id, patch)}
                  onDelete={() => handleDeleteNode(node.id)}
                  showDelete={totalRules > 1}
                />
              ) : (
                <Box
                  borderLeft="3px solid"
                  borderColor={depth === 0 ? "blue.400" : "purple.400"}
                  pl={3}
                  my={1}
                  py={1}
                  bg="gray.50/40"
                  borderRadius="sm"
                >
                  {/* Group Header */}
                  <Flex justify="space-between" align="center" mb={1.5}>
                    <HStack gap={1.5}>
                      <Box
                        px={2}
                        py={0.5}
                        borderRadius="full"
                        bg={node.conjunction === "or" ? "purple.50" : "blue.50"}
                        border="1px solid"
                        borderColor={
                          node.conjunction === "or" ? "purple.200" : "blue.200"
                        }
                        color={
                          node.conjunction === "or" ? "purple.800" : "blue.800"
                        }
                        fontSize="11px"
                        fontWeight="bold"
                      >
                        ( Group )
                      </Box>
                      <Button
                        type="button"
                        size="2xs"
                        variant="outline"
                        colorPalette={
                          node.conjunction === "or" ? "purple" : "blue"
                        }
                        bg={node.conjunction === "or" ? "purple.50" : "blue.50"}
                        h="20px"
                        px={2}
                        fontSize="10px"
                        fontWeight="bold"
                        borderRadius="full"
                        onClick={() => handleToggleConjunction(node.id)}
                        title="Toggle group match logic"
                      >
                        {node.conjunction.toUpperCase()} ▾
                      </Button>
                    </HStack>
                    <HStack gap={1}>
                      <Button
                        type="button"
                        size="2xs"
                        variant="ghost"
                        colorPalette="gray"
                        fontSize="11px"
                        h="22px"
                        px={2}
                        onClick={() => handleUngroup(node.id)}
                        title="Ungroup conditions back to parent"
                      >
                        Ungroup
                      </Button>
                      <IconButton
                        aria-label="Delete group"
                        size="2xs"
                        variant="ghost"
                        colorPalette="red"
                        h="22px"
                        w="22px"
                        minW="22px"
                        onClick={() => handleDeleteNode(node.id)}
                        title="Delete entire group"
                      >
                        <IoMdClose fontSize="14px" />
                      </IconButton>
                    </HStack>
                  </Flex>

                  {/* Group Children */}
                  {renderNodeList(node.children, node, depth + 1)}

                  {/* Add rule directly to group */}
                  <Button
                    type="button"
                    size="2xs"
                    variant="ghost"
                    colorPalette="brand"
                    fontSize="xs"
                    h="22px"
                    px={2}
                    alignSelf="flex-start"
                    mt={3}
                    mb={1}
                    onClick={() => handleAddRuleToGroup(node.id)}
                  >
                    <IoMdAdd /> Add rule to this group
                  </Button>
                </Box>
              )}
            </Box>
          );
        })}
      </Box>
    );
  };

  return (
    <VStack align="stretch" gap={2.5} width="100%" minW="0">
      {/* 1. Conditions Section (Model 2: Select & Group) */}
      <VStack align="stretch" gap={1.5}>
        <Flex justify="space-between" align="center" wrap="wrap" gap={2}>
          <HStack gap={1.5} align="center">
            <Text fontSize="xs" fontWeight="bold" color="gray.700">
              Conditions
            </Text>
            <Box
              as="span"
              fontSize="10px"
              fontWeight="semibold"
              px={1.5}
              py={0.2}
              borderRadius="full"
              bg="gray.100"
              color="gray.600"
            >
              {totalRules}
            </Box>
          </HStack>

          <HStack gap={1.5} align="center">
            {!isSelectionMode ? (
              <>
                {totalRules >= 2 && (
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    colorPalette="gray"
                    onClick={() => {
                      setIsSelectionMode(true);
                      setSelectedIds(new Set());
                    }}
                    fontSize="xs"
                    h="28px"
                    px={2.5}
                    title="Select conditions to group together"
                  >
                    <IoMdFolderOpen fontSize="14px" /> Group...
                  </Button>
                )}
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  colorPalette="gray"
                  onClick={handleReset}
                  fontSize="xs"
                  h="28px"
                  px={2}
                >
                  Reset
                </Button>
                <Button
                  type="button"
                  size="xs"
                  variant="outline"
                  colorPalette="brand"
                  onClick={handleAddCondition}
                  fontSize="xs"
                  h="28px"
                  px={2.5}
                >
                  <IoMdAdd /> Add condition
                </Button>
              </>
            ) : (
              <>
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  colorPalette="gray"
                  onClick={() => {
                    setIsSelectionMode(false);
                    setSelectedIds(new Set());
                  }}
                  fontSize="xs"
                  h="28px"
                  px={2}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="xs"
                  variant="solid"
                  colorPalette="brand"
                  onClick={handleGroupSelected}
                  disabled={!selectionValidation.valid}
                  fontSize="xs"
                  h="28px"
                  px={3}
                  title={
                    selectionValidation.valid
                      ? "Group selected conditions"
                      : selectionValidation.reason
                  }
                >
                  <IoMdFolderOpen fontSize="14px" /> Group selected (
                  {selectedIds.size})
                </Button>
              </>
            )}
          </HStack>
        </Flex>

        {/* Tree of conditions and groups */}
        <Box display="flex" flexDirection="column" gap={1.5} width="100%">
          {renderNodeList(tree.children, tree, 0)}
        </Box>
      </VStack>

      {/* 2. Target Scope Selection Bar (In the end - Choose which columns to highlight) */}
      <Box
        p={2.5}
        bg="white"
        borderRadius="md"
        border="1px solid"
        borderColor="gray.200"
      >
        <Flex
          justify="space-between"
          align="center"
          mb={isScopeSelectedColumns ? 2 : 0}
        >
          <Text fontSize="xs" fontWeight="bold" color="gray.700">
            Apply Highlight To:
          </Text>
          <HStack gap={3}>
            {/* Entire Row Option (Default) */}
            <Flex
              align="center"
              gap={1.5}
              cursor="pointer"
              onClick={() => onUpdateHighlightScope("entire_row")}
            >
              <Box
                w="13px"
                h="13px"
                borderRadius="full"
                border="1.5px solid"
                borderColor={!isScopeSelectedColumns ? "brand.500" : "gray.300"}
                bg="white"
                display="flex"
                alignItems="center"
                justifyContent="center"
              >
                {!isScopeSelectedColumns && (
                  <Box w="5px" h="5px" borderRadius="full" bg="brand.500" />
                )}
              </Box>
              <Text fontSize="xs" color="gray.700" fontWeight="medium">
                Entire Row
              </Text>
            </Flex>

            {/* Chosen Columns Option */}
            <Flex
              align="center"
              gap={1.5}
              cursor="pointer"
              onClick={() => onUpdateHighlightScope("selected_columns")}
            >
              <Box
                w="13px"
                h="13px"
                borderRadius="full"
                border="1.5px solid"
                borderColor={isScopeSelectedColumns ? "brand.500" : "gray.300"}
                bg="white"
                display="flex"
                alignItems="center"
                justifyContent="center"
              >
                {isScopeSelectedColumns && (
                  <Box w="5px" h="5px" borderRadius="full" bg="brand.500" />
                )}
              </Box>
              <Text fontSize="xs" color="gray.700" fontWeight="medium">
                Chosen Columns
              </Text>
            </Flex>
          </HStack>
        </Flex>

        {/* Column Multi-Select Picker (Shown ONLY when Chosen Columns is selected) */}
        {isScopeSelectedColumns && (
          <Box position="relative" ref={dropdownRef} mt={1.5}>
            <Button
              size="xs"
              variant="outline"
              width="100%"
              justifyContent="space-between"
              onClick={() => setIsColumnDropdownOpen(!isColumnDropdownOpen)}
              bg="gray.50"
              fontSize="xs"
              fontWeight="normal"
              h="28px"
              px={2.5}
            >
              <HStack gap={1.5} overflow="hidden">
                <Box color="brand.600" fontSize="13px">
                  <MdFilterList />
                </Box>
                <Text
                  fontSize="xs"
                  color={targetColumns.length === 0 ? "gray.400" : "gray.800"}
                  truncate
                >
                  {targetColumns.length === 0
                    ? "Select columns to highlight..."
                    : `${targetColumns.length} Selected: ${targetColumns.join(", ")}`}
                </Text>
              </HStack>
              <Text fontSize="9px" color="gray.400">
                {isColumnDropdownOpen ? "▲" : "▼"}
              </Text>
            </Button>

            {/* Popover Checklist */}
            {isColumnDropdownOpen && (
              <Box
                position="absolute"
                bottom="calc(100% + 4px)"
                left="0"
                zIndex={1000}
                bg="white"
                shadow="xl"
                border="1px solid"
                borderColor="gray.200"
                borderRadius="md"
                p={2}
                w="100%"
                minW="220px"
              >
                {/* Search & Actions */}
                <HStack gap={1} mb={1.5}>
                  <Box color="gray.400" fontSize="13px" pl={0.5}>
                    <MdSearch />
                  </Box>
                  <Input
                    size="xs"
                    placeholder="Search columns..."
                    value={colSearchQuery}
                    onChange={(e) => setColSearchQuery(e.target.value)}
                    height="24px"
                    fontSize="xs"
                  />
                </HStack>
                <Flex justify="space-between" mb={1} px={0.5}>
                  <Text
                    fontSize="xs"
                    color="brand.600"
                    cursor="pointer"
                    fontWeight="semibold"
                    onClick={handleSelectAllColumns}
                  >
                    Select All
                  </Text>
                  <Text
                    fontSize="xs"
                    color="gray.500"
                    cursor="pointer"
                    fontWeight="semibold"
                    onClick={handleClearColumns}
                  >
                    Clear All
                  </Text>
                </Flex>

                {/* Column Checklist items */}
                <Box
                  display="flex"
                  flexDirection="column"
                  gap="2px"
                  maxH="150px"
                  overflowY="auto"
                  pr={0.5}
                >
                  {filteredCols.map((col) => {
                    const isChecked = targetColumns.includes(col);
                    return (
                      <Flex
                        key={col}
                        minH="29px"
                        align="center"
                        gap={2}
                        px={2}
                        py={1}
                        borderRadius="sm"
                        _hover={{ bg: "gray.50" }}
                        cursor="pointer"
                        onClick={() => handleToggleColumn(col)}
                      >
                        <Checkbox.Root
                          size="xs"
                          colorPalette="brand"
                          checked={isChecked}
                          pointerEvents="none"
                        >
                          <Checkbox.HiddenInput />
                          <Checkbox.Control />
                        </Checkbox.Root>
                        <Text
                          as="span"
                          m={0}
                          p={0}
                          fontSize="xs"
                          color="gray.700"
                          truncate
                          userSelect="none"
                          lineHeight="normal"
                        >
                          {col}
                        </Text>
                      </Flex>
                    );
                  })}
                  {filteredCols.length === 0 && (
                    <Box px={2} py={2.5} textAlign="center">
                      <Text
                        as="span"
                        m={0}
                        p={0}
                        fontSize="2xs"
                        color="gray.400"
                      >
                        No matching columns
                      </Text>
                    </Box>
                  )}
                </Box>
              </Box>
            )}
          </Box>
        )}
      </Box>

      {/* 3. User Written / Editable Condition Expression */}
      <Box
        p={2}
        w="100%"
        minW="0"
        bg={
          isEditingText
            ? parseError
              ? "red.50/50"
              : "green.50/50"
            : "blue.50/60"
        }
        border="1px solid"
        borderColor={
          isEditingText ? (parseError ? "red.300" : "green.300") : "blue.200"
        }
        borderRadius="md"
        transition="all 0.2s"
      >
        {!isEditingText ? (
          <Flex
            justify="space-between"
            align="center"
            gap={1.5}
            w="100%"
            minW="0"
          >
            <HStack
              gap={1.5}
              align="center"
              flex="1"
              minW="0"
              overflow="hidden"
            >
              <Text
                as="span"
                fontSize="xs"
                fontWeight="bold"
                color="blue.950"
                whiteSpace="nowrap"
                flexShrink={0}
              >
                Rule:
              </Text>
              <Box
                flex="1"
                minW="0"
                overflowX="auto"
                css={{
                  "&::-webkit-scrollbar": { display: "none" },
                  scrollbarWidth: "none",
                  msOverflowStyle: "none",
                }}
              >
                <Text
                  fontSize="xs"
                  color="blue.900"
                  fontWeight="medium"
                  lineHeight="normal"
                  fontFamily="mono"
                  whiteSpace="nowrap"
                >
                  {summary || "(No condition specified)"}
                </Text>
              </Box>
            </HStack>

            <Tooltip content="Edit expression as text">
              <IconButton
                aria-label="Edit expression"
                size="2xs"
                variant="ghost"
                colorPalette="blue"
                h="20px"
                w="20px"
                minW="20px"
                borderRadius="sm"
                flexShrink={0}
                onClick={handleStartTextEdit}
              >
                <MdEdit fontSize="13px" />
              </IconButton>
            </Tooltip>
          </Flex>
        ) : (
          <VStack align="stretch" gap={1.5}>
            <Flex justify="space-between" align="center">
              <HStack gap={1} align="center">
                <Text fontSize="xs" fontWeight="bold" color="gray.700">
                  Expression
                </Text>
                <Tooltip content="Supports =, !=, >, <, >=, <=, between, contains, starts with, ends with, is empty, and/or parentheses">
                  <Box
                    as="span"
                    color="gray.400"
                    cursor="help"
                    fontSize="13px"
                    display="inline-flex"
                  >
                    <MdInfoOutline />
                  </Box>
                </Tooltip>
              </HStack>

              <Box
                fontSize="10px"
                fontWeight="semibold"
                color={parseError ? "red.700" : "green.700"}
                bg={parseError ? "red.100" : "green.100"}
                px={1.5}
                py={0.2}
                borderRadius="sm"
                whiteSpace="nowrap"
              >
                {parseError ? "Invalid" : "✓ Valid"}
              </Box>
            </Flex>

            <Input
              ref={editInputRef}
              size="xs"
              h="28px"
              fontSize="xs"
              fontFamily="mono"
              bg="white"
              value={editText}
              onChange={(e) => handleTextChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !parseError && parsedTree) {
                  e.preventDefault();
                  handleApplyTextEdit();
                } else if (e.key === "Escape") {
                  handleCancelTextEdit();
                }
              }}
              borderColor={parseError ? "red.400" : "green.500"}
              _focus={{
                borderColor: parseError ? "red.500" : "green.600",
                boxShadow: parseError
                  ? "0 0 0 1px var(--chakra-colors-red-500)"
                  : "0 0 0 1px var(--chakra-colors-green-500)",
              }}
              placeholder='e.g. (ORDER_ID = "1000" OR CUSTOMER_NAME = "Rahul") AND UNIT_PRICE > 500'
              autoFocus
            />

            {parseError && (
              <Text fontSize="2xs" color="red.600" fontWeight="medium">
                ⚠ {parseError}
              </Text>
            )}

            {displaySuggestions.length > 0 && (
              <Box
                p={1.5}
                bg={parseResult?.unknownField ? "orange.50/80" : "gray.50"}
                border="1px dashed"
                borderColor={
                  parseResult?.unknownField ? "orange.300" : "gray.200"
                }
                borderRadius="md"
              >
                <Flex justify="space-between" align="center" mb={1}>
                  <Text
                    fontSize="10px"
                    fontWeight="semibold"
                    color={
                      parseResult?.unknownField ? "orange.800" : "gray.600"
                    }
                  >
                    {parseResult?.unknownField
                      ? `Matching columns for "${parseResult.unknownField}":`
                      : "Matching columns:"}
                  </Text>
                  <Text fontSize="9px" color="gray.400">
                    Click to auto-fill
                  </Text>
                </Flex>
                <Flex wrap="wrap" gap={1} maxH="80px" overflowY="auto">
                  {displaySuggestions.map((col) => {
                    const isDirectMatch =
                      parseResult?.unknownField &&
                      col
                        .toLowerCase()
                        .includes(parseResult.unknownField.toLowerCase());
                    return (
                      <Button
                        key={col}
                        type="button"
                        size="2xs"
                        h="20px"
                        px={2}
                        fontSize="10px"
                        fontWeight="medium"
                        variant={isDirectMatch ? "solid" : "subtle"}
                        colorPalette={isDirectMatch ? "brand" : "gray"}
                        borderRadius="sm"
                        cursor="pointer"
                        _hover={{
                          transform: "translateY(-1px)",
                          boxShadow: "xs",
                        }}
                        transition="all 0.15s"
                        onClick={() => handleSelectSuggestedColumn(col)}
                      >
                        + {col}
                      </Button>
                    );
                  })}
                </Flex>
              </Box>
            )}

            <Flex justify="flex-end" gap={1.5} align="center">
              <Button
                type="button"
                size="2xs"
                variant="outline"
                h="20px"
                px={2}
                fontSize="10px"
                onClick={handleCancelTextEdit}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="2xs"
                colorPalette="brand"
                h="20px"
                px={2.5}
                fontSize="10px"
                disabled={!!parseError || !parsedTree}
                onClick={handleApplyTextEdit}
              >
                Apply
              </Button>
            </Flex>
          </VStack>
        )}
      </Box>
    </VStack>
  );
}
