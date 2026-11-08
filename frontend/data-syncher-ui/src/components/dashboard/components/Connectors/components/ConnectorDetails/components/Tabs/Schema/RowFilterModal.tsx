import {
  type CSSProperties,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  IconButton,
  Input,
  NativeSelect,
  Portal,
  Skeleton,
  Text,
} from "@chakra-ui/react";

import { IoMdAdd, IoMdTrash } from "react-icons/io";

import useFetchTableFields from "@/queryOptions/connector/schema/useFetchTableFields";
import {
  type BasicFilterCondition,
  type FilterNode,
  type RowFilterConfig,
  type TableFieldInfo,
} from "@/types/connectors";

import { isPrimaryKey } from "../ReverseSchema/utils/validation";
import { getEffectiveRowFilter } from "./utils/filterUtils";

interface RowFilterModalProps {
  open: boolean;
  onClose: () => void;
  tableName: string;
  connectionId: number;
  initialRowFilter?: RowFilterConfig | null;
  onSave: (_config: RowFilterConfig | null) => Promise<void>;
  isSaving?: boolean;
  isInitialSyncDone?: boolean;
  loadMethod?: string | null;
}

const FILTER_TYPE_BY_EDM_TYPE: Record<string, string | null> = {
  "Edm.String": "string",
  "Edm.Guid": "string",
  "Edm.Boolean": "boolean",
  "Edm.Decimal": "numeric",
  "Edm.Double": "numeric",
  "Edm.Single": "numeric",
  "Edm.Int16": "numeric",
  "Edm.Int32": "numeric",
  "Edm.Int64": "numeric",
  "Edm.Byte": "numeric",
  "Edm.SByte": "numeric",
  "Edm.DateTime": "datetime",
  "Edm.DateTimeOffset": "datetime",
  "Edm.Time": "time",
  "Edm.Binary": "string",
};

const FieldLabel = ({ children }: { children: React.ReactNode }) => (
  <Text fontSize="2xs" fontWeight="bold" color="gray.500" mb={0}>
    {children}
  </Text>
);

const LabeledSelect = ({
  value,
  onChange,
  disabled,
  children,
}: {
  value: string;
  onChange: (_e: React.ChangeEvent<HTMLSelectElement>) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) => (
  <NativeSelect.Root size="xs" disabled={disabled}>
    <NativeSelect.Field value={value} onChange={onChange}>
      {children}
    </NativeSelect.Field>
    <NativeSelect.Indicator />
  </NativeSelect.Root>
);

const SummaryChip = ({ children }: { children: React.ReactNode }) => (
  <Box
    bg="gray.100"
    px={2}
    py={0.5}
    borderRadius="md"
    borderWidth="1px"
    borderColor="gray.200"
  >
    {children}
  </Box>
);

const toCamelCase = (str: string): string => {
  if (!str) return "";
  const clean = str.startsWith("Edm.") ? str.slice(4) : str;
  return clean.charAt(0).toLowerCase() + clean.slice(1);
};

const KEYWORD_TYPE_MAP: [string[], "boolean" | "numeric" | "datetime"][] = [
  [["boolean"], "boolean"],
  [
    ["int", "decimal", "double", "float", "short", "long", "byte", "number"],
    "numeric",
  ],
  [["date", "time", "instant"], "datetime"],
];

const getFieldType = (
  info: TableFieldInfo | string | null | undefined,
): "boolean" | "numeric" | "datetime" | "time" | "string" => {
  if (!info) return "string";
  if (typeof info === "object" && info.filter_type) return info.filter_type;
  const edmType = typeof info === "string" ? "" : info.edm_type || "";
  const mapped = FILTER_TYPE_BY_EDM_TYPE[edmType ?? ""];
  if (mapped !== undefined) {
    return (mapped || "string") as
      | "boolean"
      | "numeric"
      | "datetime"
      | "time"
      | "string";
  }

  // Fallback to keyword matching on data_type if edm_type is missing
  const typeStr = typeof info === "string" ? info : info.data_type || "string";
  const norm = typeStr.toLowerCase();
  const match = KEYWORD_TYPE_MAP.find(([keywords]) =>
    keywords.some((k) => norm.includes(k)),
  );
  return match ? match[1] : "string";
};

const getFieldDataType = (
  info: TableFieldInfo | string | null | undefined,
): string => {
  if (!info) return "unknown";
  if (typeof info === "object" && info.edm_type) {
    return toCamelCase(info.edm_type);
  }
  return typeof info === "string" ? info : info.data_type || "unknown";
};

const OP_ALIASES: Record<string, string> = {
  "=": "eq",
  "!=": "ne",
  ">": "gt",
  "<": "lt",
  ">=": "ge",
  "<=": "le",
};

const normalizeOperator = (op: string): string => {
  if (!op) return "eq";
  const norm = op.toLowerCase().trim();
  return OP_ALIASES[norm] ?? norm;
};

const formatTypedValue = (
  val: string | number | boolean | unknown,
  edmType: string,
): string | number | boolean => {
  if (val === null || val === undefined) return "";
  const mappedType = FILTER_TYPE_BY_EDM_TYPE[edmType] || "string";

  if (mappedType === "boolean") {
    if (typeof val === "boolean") return val;
    if (String(val).toLowerCase() === "true") return true;
    if (String(val).toLowerCase() === "false") return false;
    return String(val);
  }
  if (mappedType === "numeric") {
    if (typeof val === "number") return val;
    const num = Number(val);
    if (!isNaN(num) && String(val).trim() !== "") return num;
    return String(val);
  }
  return String(val);
};

const COMMON_COMPARISON_OPS = [
  { label: "Equal to (EQ)", value: "eq" },
  { label: "Not equal to (NE)", value: "ne" },
  { label: "In (IN)", value: "in" },
  { label: "Greater than (GT)", value: "gt" },
  { label: "Greater than or equal to (GE)", value: "ge" },
  { label: "Less than (LT)", value: "lt" },
  { label: "Less than or equal to (LE)", value: "le" },
];

const OPERATORS_BY_TYPE: Record<string, { label: string; value: string }[]> = {
  boolean: [{ label: "Equal to (EQ)", value: "eq" }],
  numeric: COMMON_COMPARISON_OPS,
  datetime: COMMON_COMPARISON_OPS,
  time: COMMON_COMPARISON_OPS,
  string: [
    { label: "Equal to (EQ)", value: "eq" },
    { label: "Not equal to (NE)", value: "ne" },
    { label: "In (IN)", value: "in" },
    { label: "Starts with (Startswith)", value: "startswith" },
  ],
};

const getOperatorsForType = (
  type: "boolean" | "numeric" | "datetime" | "time" | "string",
) => OPERATORS_BY_TYPE[type] ?? OPERATORS_BY_TYPE.string;

const normalizeFilterRestriction = (restriction?: string | null) => {
  if (restriction === "interval-boundaries") return "interval";
  return restriction || null;
};

const getOperatorsForRestriction = (
  type: "boolean" | "numeric" | "datetime" | "time" | "string",
  restriction?: string | null,
) => {
  switch (normalizeFilterRestriction(restriction)) {
    case "single-value":
      return [{ label: "Equal to (EQ)", value: "eq" }];
    case "multi-value":
      return [
        { label: "Equal to (EQ)", value: "eq" },
        { label: "In (IN)", value: "in" },
      ];
    case "interval":
      return [{ label: "Range (ge & le)", value: "ge_le" }];
    default:
      return getOperatorsForType(type);
  }
};
const ColumnSelect = ({
  value,
  onChange,
  fieldsList,
  disabled,
}: {
  value: string;
  onChange: (_val: string) => void;
  fieldsList: string[];
  disabled: boolean;
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dropdownStyle, setDropdownStyle] = useState<CSSProperties>({});

  useEffect(() => {
    setSearch(value);
  }, [value]);

  const updateDropdownPosition = () => {
    if (inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setDropdownStyle({
        position: "fixed",
        top: rect.bottom + 4,
        left: rect.left,
        width: rect.width,
        zIndex: 9999,
      });
    }
  };

  // Standard dropdown pattern: close on blur but allow onMouseDown to fire first (150ms delay)
  const handleBlur = () => {
    setTimeout(() => {
      setIsOpen(false);
      setSearch(value);
    }, 150);
  };

  const handleFocus = () => {
    updateDropdownPosition();
    setIsOpen(true);
  };

  // If the user hasn't typed a new search (search is just the current value), show all options
  const isSearchActive = search && search !== value;
  const filteredFields = fieldsList.filter(
    (col) =>
      !isSearchActive || col.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <Box position="relative" w="100%">
      <Input
        ref={inputRef}
        size="xs"
        placeholder="Select or search column..."
        value={search}
        disabled={disabled}
        onChange={(e) => {
          setSearch(e.target.value);
          updateDropdownPosition();
          setIsOpen(true);
        }}
        onFocus={handleFocus}
        onBlur={handleBlur}
      />
      {isOpen && !disabled && (
        <Box
          style={dropdownStyle}
          maxH="200px"
          overflowY="auto"
          bg="white"
          boxShadow="lg"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="md"
        >
          {filteredFields.length > 0 ? (
            filteredFields.map((col) => {
              return (
                <Box
                  key={col}
                  px={3}
                  py={2}
                  fontSize="xs"
                  cursor="pointer"
                  userSelect="none"
                  _hover={{ bg: "brand.50" }}
                  // onMouseDown fires before onBlur, so selection happens before blur closes the dropdown
                  onMouseDown={() => {
                    onChange(col);
                    setSearch(col);
                    setIsOpen(false);
                  }}
                >
                  {col}
                </Box>
              );
            })
          ) : (
            <Box px={3} py={2} fontSize="xs" color="gray.500">
              No columns found
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
};

const MultipleValueChips = ({
  values,
  onChange,
  disabled,
  type = "text",
  placeholder,
}: {
  values: string[];
  onChange: (_vals: string[]) => void;
  disabled: boolean;
  type?: string;
  placeholder?: string;
}) => {
  const [inputValue, setInputValue] = useState("");
  const [isFocused, setIsFocused] = useState(false);

  const handleAdd = (val: string) => {
    const trimmed = val.trim();
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed]);
    }
  };

  const handleRemove = (valToRemove: string) => {
    onChange(values.filter((v) => v !== valToRemove));
  };

  const isDateOrTimeType = ["date", "month", "datetime-local", "time"].includes(
    type,
  );
  const currentInputType = isDateOrTimeType
    ? inputValue || isFocused
      ? type
      : "text"
    : type;

  return (
    <Flex
      wrap="wrap"
      gap={1.5}
      align="center"
      borderWidth="1px"
      borderColor={disabled ? "gray.200" : "gray.200"}
      borderRadius="md"
      px={2}
      py={1}
      minH="32px"
      w="100%"
      bg={disabled ? "gray.50" : "white"}
      _focusWithin={{
        borderColor: "brand.500",
        boxShadow: "0 0 0 1px var(--chakra-colors-brand-500)",
      }}
    >
      {values.map((val) => (
        <Flex
          key={val}
          align="center"
          gap={1}
          bg={disabled ? "gray.200" : "brand.50"}
          color={disabled ? "gray.600" : "brand.800"}
          px={2.5}
          py={0.5}
          borderRadius="full"
          borderWidth="1px"
          borderColor={disabled ? "gray.300" : "brand.200"}
          fontSize="xs"
        >
          <Text fontSize="xs" fontWeight="semibold">
            {val}
          </Text>
          {!disabled && (
            <Text
              fontSize="xs"
              fontWeight="bold"
              cursor="pointer"
              onClick={(e) => {
                e.stopPropagation();
                handleRemove(val);
              }}
              _hover={{ color: "red.600" }}
              ml={1}
            >
              &times;
            </Text>
          )}
        </Flex>
      ))}
      {!disabled && (
        <Input
          variant="flushed"
          border="none"
          _focus={{ outline: "none", boxShadow: "none" }}
          size="xs"
          flex="1"
          minW="110px"
          type={currentInputType}
          disabled={disabled}
          placeholder={
            values.length === 0
              ? placeholder || "Type value and press Enter"
              : "Add value..."
          }
          value={inputValue}
          onChange={(e) => {
            const val = e.target.value;
            setInputValue(val);
            if (isDateOrTimeType && val) {
              handleAdd(val);
              setInputValue("");
            }
          }}
          onFocus={(e) => {
            setIsFocused(true);
            if (isDateOrTimeType) {
              try {
                e.target.showPicker();
              } catch {
                /* ignore */
              }
            }
          }}
          onBlur={() => {
            setIsFocused(false);
            if (inputValue.trim()) {
              handleAdd(inputValue);
              setInputValue("");
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (inputValue.trim()) {
                handleAdd(inputValue);
                setInputValue("");
              }
            } else if (
              e.key === "Backspace" &&
              inputValue === "" &&
              values.length > 0
            ) {
              handleRemove(values[values.length - 1]);
            }
          }}
          px={1}
          h="24px"
        />
      )}
    </Flex>
  );
};

const FlexibleDateTimePicker = ({
  disabled,
  filterRestriction,
  fieldInfo,
  mode,
  value,
  onChange,
  fromValue,
  toValue,
  onRangeChange,
  multipleValues,
  onMultipleChange,
  granularity,
  onGranularityChange,
}: {
  disabled: boolean;
  filterRestriction?: string | null;
  fieldInfo?: TableFieldInfo | null;
  mode: "exact" | "range" | "multiple";
  value: string;
  onChange: (_val: string) => void;
  fromValue: string;
  toValue: string;
  onRangeChange: (_from: string, _to: string) => void;
  multipleValues: string[];
  onMultipleChange: (_vals: string[]) => void;
  granularity: "year" | "month" | "date" | "datetime-local";
  onGranularityChange: (
    _g: "year" | "month" | "date" | "datetime-local",
  ) => void;
}) => {
  const dropYearGranularity =
    filterRestriction === "single-value" || filterRestriction === "multi-value";

  const isDateFormatWithZeroPrecision =
    fieldInfo &&
    typeof fieldInfo === "object" &&
    ((fieldInfo.display_format as string)?.toLowerCase() === "date" ||
      (fieldInfo.displayFormat as string)?.toLowerCase() === "date") &&
    (String(fieldInfo.precision) === "0" || fieldInfo.precision === 0);

  // Force Date/DateTime when single-value or multi-value (drop year / year & month range granularities)
  useEffect(() => {
    if (
      dropYearGranularity &&
      (granularity === "year" || granularity === "month")
    ) {
      onGranularityChange("date");
    }
  }, [dropYearGranularity, granularity, onGranularityChange]);

  useEffect(() => {
    if (isDateFormatWithZeroPrecision && granularity === "datetime-local") {
      onGranularityChange("date");
    }
  }, [isDateFormatWithZeroPrecision, granularity, onGranularityChange]);

  const DateInput = ({
    val,
    onValChange,
    placeholder,
  }: {
    val: string;
    onValChange: (_v: string) => void;
    placeholder: string;
  }) => {
    const [isFocused, setIsFocused] = useState(false);
    const currentType =
      granularity === "year"
        ? "number"
        : val || isFocused
          ? granularity
          : "text";

    const openPicker = (el: HTMLInputElement) => {
      if (granularity !== "year") {
        if (el.type === "text") {
          el.type = granularity;
        }
        try {
          el.showPicker();
        } catch {
          /* ignore */
        }
      }
    };

    return (
      <Input
        size="xs"
        type={currentType}
        placeholder={placeholder}
        min={granularity === "year" ? "1900" : undefined}
        max={granularity === "year" ? "2100" : undefined}
        value={val}
        disabled={disabled}
        onChange={(e) => onValChange(e.target.value)}
        onFocus={(e) => {
          setIsFocused(true);
          openPicker(e.target);
        }}
        onBlur={() => setIsFocused(false)}
        onClick={(e) => openPicker(e.currentTarget)}
        flex="1"
        cursor={granularity !== "year" ? "pointer" : "text"}
      />
    );
  };

  const getPlaceholder = (prefix = "Select") => {
    switch (granularity) {
      case "year":
        return "Enter year (YYYY)";
      case "month":
        return `${prefix} month & year`;
      case "date":
        return `${prefix} date`;
      case "datetime-local":
        return `${prefix} date & time`;
      default:
        return "";
    }
  };
  return (
    <Flex gap={2} w="100%" align="flex-start" wrap="wrap">
      {/* Precision Dropdown */}
      <Box minW="130px">
        <FieldLabel>PRECISION</FieldLabel>
        <LabeledSelect
          disabled={disabled}
          value={granularity}
          onChange={(e) => {
            onGranularityChange(
              e.target.value as "year" | "month" | "date" | "datetime-local",
            );
          }}
        >
          {!dropYearGranularity && <option value="year">Year Only</option>}
          {!dropYearGranularity && <option value="month">Year & Month</option>}
          <option value="date">Date</option>
          {!isDateFormatWithZeroPrecision && (
            <option value="datetime-local">Date & Time</option>
          )}
        </LabeledSelect>
      </Box>

      {/* Input controls based on Mode */}
      <Box flex="1" minW="200px">
        <FieldLabel>
          {mode === "range"
            ? "DATE RANGE"
            : mode === "multiple"
              ? "DATE VALUES"
              : "DATE VALUE"}
        </FieldLabel>
        {mode === "exact" && (
          <DateInput
            val={value}
            onValChange={onChange}
            placeholder={getPlaceholder()}
          />
        )}

        {mode === "range" && (
          <Flex gap={2} w="100%" align="center">
            <DateInput
              val={fromValue}
              onValChange={(v) => onRangeChange(v, toValue)}
              placeholder="From"
            />
            <Text fontSize="xs" color="gray.400" flexShrink={0}>
              to
            </Text>
            <DateInput
              val={toValue}
              onValChange={(v) => onRangeChange(fromValue, v)}
              placeholder="To"
            />
          </Flex>
        )}

        {mode === "multiple" && (
          <MultipleValueChips
            type={granularity === "year" ? "number" : granularity}
            values={multipleValues}
            onChange={onMultipleChange}
            disabled={disabled}
            placeholder={getPlaceholder("Add")}
          />
        )}
      </Box>
    </Flex>
  );
};

interface UICondition {
  id: string;
  isNot?: boolean;
  column: string;
  mode: "exact" | "range" | "multiple";
  operator: string;
  value: string;
  fromValue: string;
  toValue: string;
  multipleValues: string[];
  granularity: "year" | "month" | "date" | "datetime-local";
  edm_type: string;
}

const FilterConditionEditor = ({
  condition,
  fieldsList,
  tableFields,
  onChange,
  isFieldsLoading,
  disabled = false,
}: {
  condition: UICondition;
  fieldsList: string[];
  tableFields: Record<string, TableFieldInfo | string>;
  onChange: (_updated: Partial<UICondition>) => void;
  isFieldsLoading: boolean;
  disabled?: boolean;
}) => {
  const fieldInfo = tableFields[condition.column];
  const restriction =
    typeof fieldInfo === "object" && fieldInfo !== null
      ? normalizeFilterRestriction(fieldInfo.filter_restriction)
      : null;
  const columnType = isFieldsLoading
    ? "string"
    : getFieldType(fieldInfo || "string");
  const operators = getOperatorsForRestriction(columnType, restriction);
  const isComponentDisabled = disabled || isFieldsLoading;

  const buildResetFields = (
    mode: "exact" | "range" | "multiple",
    colType: ReturnType<typeof getFieldType>,
  ) => ({
    mode,
    operator:
      mode === "exact"
        ? getOperatorsForRestriction(colType, null)[0].value
        : mode === "range"
          ? "ge_le"
          : "in",
    value: "",
    fromValue: "",
    toValue: "",
    multipleValues: [],
  });

  const handleColumnChange = (column: string) => {
    const info = tableFields[column];
    const edm_type =
      typeof info === "string"
        ? "Edm.String"
        : (info?.edm_type ?? "Edm.String");
    const colType = getFieldType(info || "string");
    const newRestriction = normalizeFilterRestriction(
      typeof info === "object" && info !== null
        ? info.filter_restriction
        : null,
    );

    let defaultMode: "exact" | "range" | "multiple" = "exact";
    if (newRestriction === "interval") {
      defaultMode = "range";
    } else if (newRestriction === "multi-value") {
      defaultMode = "exact";
    }

    onChange({
      column,
      isNot: false,
      granularity: "date",
      edm_type,
      ...buildResetFields(defaultMode, colType),
    });
  };

  return (
    <Flex direction="column" gap={2} w="100%">
      {/* Row 1: Column + Operator */}
      <Flex gap={2} w="100%" wrap="wrap">
        <Box flex="2" minW="200px">
          <FieldLabel>COLUMN</FieldLabel>
          <ColumnSelect
            value={condition.column}
            onChange={handleColumnChange}
            fieldsList={fieldsList}
            disabled={isComponentDisabled}
          />
        </Box>
        <Box flex="1" minW="130px">
          <FieldLabel>OPERATOR</FieldLabel>
          {restriction !== "interval" && condition.mode !== "range" ? (
            <LabeledSelect
              disabled={isComponentDisabled}
              value={condition.operator}
              onChange={(e) => {
                const op = e.target.value;
                if (op === "in") {
                  onChange({
                    operator: "in",
                    mode: "multiple",
                    value: "",
                    multipleValues: [],
                  });
                } else {
                  onChange({
                    operator: op,
                    mode: "exact",
                    value: "",
                    fromValue: "",
                    toValue: "",
                    multipleValues: [],
                  });
                }
              }}
            >
              {condition.column === "" && (
                <option value="">Select operator</option>
              )}
              {operators.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </LabeledSelect>
          ) : (
            <Input
              size="xs"
              disabled
              value={
                condition.mode === "range"
                  ? "Range (ge & le)"
                  : condition.mode === "multiple"
                    ? "In (in)"
                    : restriction === "single-value"
                      ? "Equal to (eq)"
                      : ""
              }
            />
          )}
        </Box>
      </Flex>

      {/* Row 3: Values Inputs */}
      {condition.operator !== "isnull" &&
        condition.operator !== "isnotnull" && (
          <Box w="100%">
            {condition.mode === "range" && columnType !== "datetime" ? (
              <Box>
                <FieldLabel>
                  {columnType === "numeric" ? "NUMERIC RANGE" : "RANGE"}
                </FieldLabel>
                <Flex gap={2} w="100%" align="center">
                  <Input
                    flex="1"
                    size="xs"
                    type={columnType === "numeric" ? "number" : "text"}
                    step={columnType === "numeric" ? "any" : undefined}
                    placeholder="From"
                    value={condition.fromValue}
                    onChange={(e) => onChange({ fromValue: e.target.value })}
                    disabled={isComponentDisabled}
                  />
                  <Text fontSize="xs" color="gray.400" flexShrink={0}>
                    to
                  </Text>
                  <Input
                    flex="1"
                    size="xs"
                    type={columnType === "numeric" ? "number" : "text"}
                    step={columnType === "numeric" ? "any" : undefined}
                    placeholder="To"
                    value={condition.toValue}
                    onChange={(e) => onChange({ toValue: e.target.value })}
                    disabled={isComponentDisabled}
                  />
                </Flex>
                {columnType === "string" && (
                  <Text fontSize="2xs" color="orange.600" mt={1}>
                    String ranges use lexicographic comparison. Use fixed-length
                    or leading-zero values.
                  </Text>
                )}
              </Box>
            ) : columnType === "boolean" ? (
              <Box maxW="200px">
                <FieldLabel>BOOLEAN VALUE</FieldLabel>
                <LabeledSelect
                  disabled={isComponentDisabled}
                  value={condition.value}
                  onChange={(e) => onChange({ value: e.target.value })}
                >
                  <option value="">Select boolean value</option>
                  <option value="true">True</option>
                  <option value="false">False</option>
                </LabeledSelect>
              </Box>
            ) : columnType === "numeric" ? (
              condition.mode === "multiple" ? (
                <Box>
                  <FieldLabel>NUMERIC VALUES</FieldLabel>
                  <MultipleValueChips
                    type="number"
                    values={condition.multipleValues}
                    onChange={(vals) => onChange({ multipleValues: vals })}
                    disabled={isComponentDisabled}
                  />
                </Box>
              ) : (
                <Box>
                  <FieldLabel>NUMERIC VALUE</FieldLabel>
                  <Input
                    size="xs"
                    type="number"
                    step="any"
                    disabled={isComponentDisabled}
                    placeholder="Define the number you want to filter by."
                    value={condition.value}
                    onChange={(e) => onChange({ value: e.target.value })}
                  />
                </Box>
              )
            ) : columnType === "datetime" ? (
              <FlexibleDateTimePicker
                disabled={isComponentDisabled}
                filterRestriction={restriction}
                fieldInfo={typeof fieldInfo === "object" ? fieldInfo : null}
                mode={condition.mode}
                value={condition.value}
                onChange={(val) => onChange({ value: val })}
                fromValue={condition.fromValue}
                toValue={condition.toValue}
                onRangeChange={(from, to) =>
                  onChange({ fromValue: from, toValue: to })
                }
                multipleValues={condition.multipleValues}
                onMultipleChange={(vals) => onChange({ multipleValues: vals })}
                granularity={condition.granularity || "date"}
                onGranularityChange={(g) => onChange({ granularity: g })}
              />
            ) : columnType === "time" ? (
              condition.operator === "in" ? (
                <Box>
                  <FieldLabel>TIME VALUES</FieldLabel>
                  <MultipleValueChips
                    type="time"
                    values={condition.multipleValues}
                    onChange={(vals) => onChange({ multipleValues: vals })}
                    disabled={isComponentDisabled}
                  />
                </Box>
              ) : (
                <Box>
                  <FieldLabel>TIME VALUE</FieldLabel>
                  <Input
                    size="xs"
                    type="time"
                    disabled={isComponentDisabled}
                    placeholder="Select time"
                    value={condition.value}
                    onChange={(e) => onChange({ value: e.target.value })}
                  />
                </Box>
              )
            ) : condition.mode === "multiple" ? (
              <Box>
                <FieldLabel>STRING VALUES</FieldLabel>
                <MultipleValueChips
                  type="text"
                  values={condition.multipleValues}
                  onChange={(vals) => onChange({ multipleValues: vals })}
                  disabled={isComponentDisabled}
                />
              </Box>
            ) : (
              <Box>
                <FieldLabel>STRING VALUE</FieldLabel>
                <Input
                  size="xs"
                  type="text"
                  disabled={isComponentDisabled}
                  placeholder={
                    condition.operator === "IN"
                      ? "value1, value2, ..."
                      : "Define the string to filter by"
                  }
                  value={condition.value}
                  onChange={(e) => onChange({ value: e.target.value })}
                />
              </Box>
            )}
          </Box>
        )}
    </Flex>
  );
};
const parseSingleNodeToUI = (
  node: FilterNode,
  tableFields: Record<string, TableFieldInfo | string>,
  isNotParent = false,
): UICondition[] => {
  if (!node || typeof node !== "object") return [];

  if ("not" in node && node.not) {
    return parseSingleNodeToUI(node.not, tableFields, true);
  }

  if ("conditions" in node && Array.isArray(node.conditions)) {
    const results: UICondition[] = [];
    for (const child of node.conditions) {
      results.push(...parseSingleNodeToUI(child, tableFields, isNotParent));
    }
    return results;
  }

  const cond = node as BasicFilterCondition;
  const col = cond.column || cond.field || "";
  if (!col) return [];

  const info = (tableFields as Record<string, Record<string, unknown>>)[col];
  const restriction = normalizeFilterRestriction(
    typeof info === "object" && info !== null
      ? (info.filter_restriction as string)
      : null,
  );

  const detectGranularity = (
    val: string | number | boolean | unknown,
  ): "year" | "month" | "date" | "datetime-local" => {
    const str = String(val || "");
    if (!str) return "date";
    if (str.length === 4) return "year";
    if (str.length === 7 && str.includes("-")) return "month";
    if (str.length === 10 && str.includes("-")) return "date";
    if (str.includes("T")) return "datetime-local";
    return "date";
  };

  const normOp = normalizeOperator(cond.operator || "eq");
  const edm_type =
    cond.edm_type ||
    (typeof info === "string"
      ? "Edm.String"
      : ((info?.edm_type as string) ?? "Edm.String"));

  if (normOp === "in" || Array.isArray(cond.value)) {
    const valuesList: string[] = Array.isArray(cond.value)
      ? cond.value.map((s) => String(s).trim()).filter(Boolean)
      : cond.value
        ? String(cond.value)
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];

    return [
      {
        id: Math.random().toString(36).substring(2, 9),
        isNot: isNotParent,
        column: col,
        mode: "multiple",
        operator: "in",
        value: "",
        fromValue: "",
        toValue: "",
        multipleValues: valuesList,
        granularity: detectGranularity(valuesList[0] || ""),
        edm_type,
      },
    ];
  }

  const isIntervalBoundaries =
    restriction === "interval-boundaries" || restriction === "interval";
  const isMultiValue = restriction === "multi-value";

  return [
    {
      id: Math.random().toString(36).substring(2, 9),
      isNot: isNotParent,
      column: col,
      mode: isIntervalBoundaries
        ? "range"
        : isMultiValue && normOp === "in"
          ? "multiple"
          : "exact",
      operator: normOp,
      value: String(cond.value ?? ""),
      fromValue: isIntervalBoundaries ? String(cond.value ?? "") : "",
      toValue: "",
      multipleValues:
        isMultiValue && normOp === "in" ? [String(cond.value ?? "")] : [],
      granularity: detectGranularity(cond.value),
      edm_type,
    },
  ];
};

const parseBackendToUI = (
  backendData: RowFilterConfig | FilterNode[] | null | undefined,
  tableFields: Record<string, TableFieldInfo | string>,
): { logic: "and" | "or"; conditions: UICondition[] } => {
  if (!backendData) return { logic: "and", conditions: [] };

  let topLogic: "and" | "or" = "and";
  let rawConditions: FilterNode[] = [];

  if (Array.isArray(backendData)) {
    rawConditions = backendData;
  } else if (typeof backendData === "object") {
    topLogic = (
      backendData.logic ||
      backendData.operator ||
      "and"
    ).toLowerCase() as "and" | "or";
    rawConditions = backendData.conditions || [];
  }

  const uiConds: UICondition[] = [];
  let i = 0;
  while (i < rawConditions.length) {
    const current = rawConditions[i];

    if (
      current &&
      !("not" in current) &&
      !("conditions" in current) &&
      ((current as BasicFilterCondition).column ||
        (current as BasicFilterCondition).field)
    ) {
      const basicCurrent = current as BasicFilterCondition;
      const col = basicCurrent.column || basicCurrent.field || "";
      const info = (tableFields as Record<string, Record<string, unknown>>)[
        col
      ];
      const colType = getFieldType(info || "string");
      const restriction = normalizeFilterRestriction(
        typeof info === "object" && info !== null
          ? (info.filter_restriction as string)
          : null,
      );
      const normOp = normalizeOperator(basicCurrent.operator);

      if (
        (colType === "datetime" ||
          colType === "numeric" ||
          restriction === "interval") &&
        (normOp === "ge" || normOp === "gt") &&
        i + 1 < rawConditions.length
      ) {
        const next = rawConditions[i + 1];
        if (
          next &&
          !("not" in next) &&
          !("conditions" in next) &&
          ((next as BasicFilterCondition).column === col ||
            (next as BasicFilterCondition).field === col)
        ) {
          const nextNormOp = normalizeOperator(
            (next as BasicFilterCondition).operator,
          );
          if (nextNormOp === "le" || nextNormOp === "lt") {
            const detectGranularity = (val: string) => {
              if (!val) return "date" as const;
              if (val.length === 4) return "year" as const;
              if (val.length === 7 && val.includes("-"))
                return "month" as const;
              if (val.length === 10 && val.includes("-"))
                return "date" as const;
              if (val.includes("T")) return "datetime-local" as const;
              return "date" as const;
            };

            uiConds.push({
              id: Math.random().toString(36).substring(2, 9),
              isNot: false,
              column: col,
              mode: "range",
              operator: "ge_le",
              value: "",
              fromValue: String(basicCurrent.value ?? ""),
              toValue: String((next as BasicFilterCondition).value ?? ""),
              multipleValues: [],
              granularity: detectGranularity(String(basicCurrent.value ?? "")),
              edm_type: basicCurrent.edm_type || "",
            });
            i += 2;
            continue;
          }
        }
      }
    }

    const parsedNodes = parseSingleNodeToUI(current, tableFields);
    uiConds.push(...parsedNodes);
    i++;
  }

  return { logic: topLogic === "or" ? "or" : "and", conditions: uiConds };
};

const serializeUIToBackend = (
  topLogic: "and" | "or",
  uiConds: UICondition[],
  tableFields: Record<string, TableFieldInfo | string>,
): RowFilterConfig => {
  const backendConds: FilterNode[] = [];

  for (const c of uiConds) {
    if (!c.column) continue;
    const fieldInfo = tableFields[c.column];
    const edm_type =
      c.edm_type ||
      (typeof fieldInfo === "string"
        ? "Edm.String"
        : (fieldInfo?.edm_type ?? "Edm.String"));
    const isNot =
      Boolean(c.isNot) &&
      !normalizeFilterRestriction(
        typeof fieldInfo === "object" && fieldInfo !== null
          ? fieldInfo.filter_restriction
          : null,
      );

    let nodePayload: FilterNode | FilterNode[];

    if (c.mode === "range") {
      const geCond: BasicFilterCondition = {
        column: c.column,
        operator: "ge",
        value: formatTypedValue(c.fromValue, edm_type),
        edm_type,
      };
      const leCond: BasicFilterCondition = {
        column: c.column,
        operator: "le",
        value: formatTypedValue(c.toValue, edm_type),
        edm_type,
      };
      if (isNot) {
        nodePayload = {
          not: {
            logic: "and",
            conditions: [geCond, leCond],
          },
        };
      } else {
        nodePayload = [geCond, leCond];
      }
    } else if (c.mode === "multiple" || c.operator === "in") {
      const typedValues = (c.multipleValues || [])
        .map((val) => formatTypedValue(val, edm_type))
        .filter((v) => v !== "" && v !== undefined && v !== null);

      const inCond: BasicFilterCondition = {
        column: c.column,
        operator: "in",
        value: typedValues,
        edm_type,
      };
      if (isNot) {
        nodePayload = { not: inCond };
      } else {
        nodePayload = inCond;
      }
    } else {
      const normOp = normalizeOperator(c.operator);
      const exactCond: BasicFilterCondition = {
        column: c.column,
        operator: normOp,
        value: formatTypedValue(c.value, edm_type),
        edm_type,
      };
      if (isNot) {
        nodePayload = { not: exactCond };
      } else {
        nodePayload = exactCond;
      }
    }

    if (Array.isArray(nodePayload)) {
      backendConds.push(...nodePayload);
    } else {
      backendConds.push(nodePayload);
    }
  }

  return {
    logic: topLogic,
    conditions: backendConds,
  };
};

const RowFilterModal = ({
  open,
  onClose,
  tableName,
  connectionId,
  initialRowFilter,
  onSave,
  isSaving = false,
  isInitialSyncDone = false,
  loadMethod = "initial",
}: RowFilterModalProps) => {
  const { data: fieldsData, isLoading: isFieldsLoading } = useFetchTableFields(
    connectionId,
    tableName,
    open,
  );

  const tableFields = useMemo(
    () =>
      fieldsData?.table_fields ??
      ({} as Record<string, TableFieldInfo | string>),
    [fieldsData?.table_fields],
  );

  const [activeSyncTable, setActiveSyncTable] = useState<string | null>(null);

  const [topLogic, setTopLogic] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<UICondition[]>([]);

  const [expandedIndices, setExpandedIndices] = useState<
    Record<number, boolean>
  >({});

  if (open && !isFieldsLoading && activeSyncTable !== tableName) {
    setActiveSyncTable(tableName);

    const activeFilter =
      initialRowFilter !== undefined
        ? initialRowFilter
        : getEffectiveRowFilter(fieldsData);
    if (activeFilter) {
      const parsed = parseBackendToUI(activeFilter, tableFields);
      setTopLogic(parsed.logic);
      setConditions(parsed.conditions);

      const initialExpanded: Record<number, boolean> = {};
      parsed.conditions.forEach((_, idx: number) => {
        initialExpanded[idx] = false; // Collapsed by default
      });
      setExpandedIndices(initialExpanded);
    } else {
      setTopLogic("and");
      setConditions([]);
      setExpandedIndices({});
    }
  } else if (!open && activeSyncTable !== null) {
    setActiveSyncTable(null);
  }

  const handleAddCondition = () => {
    const newIdx = conditions.length;
    setConditions((prev) => [
      ...prev,
      {
        id: Math.random().toString(36).substring(2, 9),
        isNot: false,
        column: "",
        mode: "exact",
        operator: "",
        value: "",
        fromValue: "",
        toValue: "",
        multipleValues: [],
        granularity: "date",
        edm_type: "",
      },
    ]);
    setExpandedIndices((prev) => ({ ...prev, [newIdx]: true }));
  };

  const handleRemoveCondition = (index: number) => {
    setConditions((prev) => prev.filter((_, i) => i !== index));
    setExpandedIndices((prev) => {
      const next = { ...prev };
      delete next[index];
      const shifted: Record<number, boolean> = {};
      Object.entries(next).forEach(([key, val]) => {
        const k = Number(key);
        if (k > index) {
          shifted[k - 1] = val;
        } else {
          shifted[k] = val;
        }
      });
      return shifted;
    });
  };

  const handleUpdateCondition = (
    index: number,
    updated: Partial<UICondition>,
  ) => {
    setConditions((prev) =>
      prev.map((cond, i) => (i === index ? { ...cond, ...updated } : cond)),
    );
  };

  const toggleExpand = (index: number) => {
    setExpandedIndices((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  const handleClearAll = () => {
    setConditions([]);
    setExpandedIndices({});
    onSave(null);
  };

  const handleSave = () => {
    const validConditions = conditions.filter((c) => {
      if (!c.column) return false;
      if (c.mode === "exact") {
        return !!c.operator;
      }
      return true;
    });

    if (validConditions.length === 0) {
      onSave(null);
    } else {
      const serialized = serializeUIToBackend(
        topLogic,
        validConditions,
        tableFields,
      );
      onSave(serialized);
    }
  };

  const isSaveDisabled = conditions.some((c) => {
    if (!c.column) return true;
    if (c.mode === "exact") {
      return c.value === "" || c.value === undefined;
    }
    if (c.mode === "range") {
      if (!c.fromValue?.trim() || !c.toValue?.trim()) return true;

      const fieldInfo = tableFields[c.column];
      const columnType = getFieldType(fieldInfo || "string");
      if (columnType === "numeric") {
        const from = Number(c.fromValue);
        const to = Number(c.toValue);
        return !Number.isFinite(from) || !Number.isFinite(to) || from > to;
      }
      return false;
    }
    if (c.mode === "multiple") {
      return c.multipleValues.length === 0;
    }
    return false;
  });

  const normalizedLoadMethod = (loadMethod || "initial").toLowerCase();
  const isOnlyPrimaryKeyAllowed = normalizedLoadMethod !== "initial";

  const fieldsList = Object.keys(tableFields)
    .filter((col) => {
      const info = tableFields[col];
      const isFilterable =
        typeof info === "object" &&
        info !== null &&
        info.filterable !== undefined
          ? info.filterable
          : true;
      if (!isFilterable) {
        return false;
      }
      const edmType = typeof info === "string" ? null : (info.edm_type ?? null);
      if (edmType && FILTER_TYPE_BY_EDM_TYPE[edmType] === null) {
        return false;
      }
      if (isOnlyPrimaryKeyAllowed) {
        const isPK = isPrimaryKey(
          col,
          typeof info === "string" ? null : info,
          fieldsData?.primary_keys,
        );
        if (!isPK) {
          return false;
        }
      }
      return true;
    })
    .sort();

  return (
    <Dialog.Root lazyMount open={open} size="lg">
      <Portal>
        <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
        <Dialog.Positioner>
          <Dialog.Content
            borderRadius="xl"
            boxShadow="2xl"
            bg="white"
            overflow="hidden"
            maxH="90vh"
            display="flex"
            flexDirection="column"
            maxW="600px"
            width="95%"
          >
            <Dialog.Header
              bg="gray.50"
              borderBottomWidth="1px"
              borderColor="gray.200"
              p={3}
            >
              <Flex
                justifyContent="space-between"
                alignItems="center"
                width="100%"
              >
                <Box>
                  <Dialog.Title
                    color="brand.800"
                    fontWeight="bold"
                    fontSize="lg"
                  >
                    Edit filter
                  </Dialog.Title>
                  {isInitialSyncDone ? (
                    <Text fontSize="xs" color="orange.600" fontWeight="medium">
                      Initial sync completed. Filter configuration is locked and
                      cannot be modified.
                    </Text>
                  ) : isOnlyPrimaryKeyAllowed ? (
                    <Text fontSize="xs" color="gray.500">
                      For Initial + Delta and Delta Only load methods, only
                      primary key columns can be filtered.
                    </Text>
                  ) : (
                    <Text fontSize="xs" color="gray.500">
                      Define filter expressions to identify which rows are
                      included in your sync.
                    </Text>
                  )}
                </Box>
                <CloseButton onClick={onClose} size="sm" />
              </Flex>
            </Dialog.Header>

            <Dialog.Body
              p={2.5}
              overflowY="auto"
              display="flex"
              flexDirection="column"
              gap={1.5}
            >
              {conditions.length > 1 && (
                <Flex
                  justify="space-between"
                  align="center"
                  bg="gray.50"
                  px={3}
                  py={1.5}
                  borderRadius="md"
                  borderWidth="1px"
                  borderColor="gray.200"
                  mb={1}
                >
                  <Text fontSize="xs" fontWeight="semibold" color="gray.600">
                    MATCH CONDITIONS WITH:
                  </Text>
                  <Flex gap={1}>
                    <Button
                      size="xs"
                      variant={topLogic === "and" ? "solid" : "outline"}
                      colorPalette={topLogic === "and" ? "brand" : "gray"}
                      onClick={() => setTopLogic("and")}
                      disabled={isInitialSyncDone}
                    >
                      AND
                    </Button>
                    <Button
                      size="xs"
                      variant={topLogic === "or" ? "solid" : "outline"}
                      colorPalette={topLogic === "or" ? "brand" : "gray"}
                      onClick={() => setTopLogic("or")}
                      disabled={isInitialSyncDone}
                    >
                      OR
                    </Button>
                  </Flex>
                </Flex>
              )}

              {conditions.map((condition, index) => {
                const isExpanded = expandedIndices[index] ?? false;
                const fieldInfo = tableFields[condition.column];
                const columnType = isFieldsLoading
                  ? "string"
                  : getFieldType(fieldInfo || "string");
                const operators = getOperatorsForRestriction(
                  columnType,
                  typeof fieldInfo === "object" && fieldInfo !== null
                    ? fieldInfo.filter_restriction
                    : null,
                );
                const restriction = normalizeFilterRestriction(
                  typeof fieldInfo === "object" && fieldInfo !== null
                    ? fieldInfo.filter_restriction
                    : null,
                );

                return (
                  <Box key={condition.id || index} width="100%">
                    {index > 0 && (
                      <Flex justify="center" my={1}>
                        <Box
                          px={2}
                          py={0.5}
                          bg="brand.50"
                          borderRadius="md"
                          borderWidth="1px"
                          borderColor="brand.200"
                        >
                          <Text
                            fontSize="2xs"
                            fontWeight="bold"
                            color="brand.700"
                          >
                            {topLogic.toUpperCase()}
                          </Text>
                        </Box>
                      </Flex>
                    )}

                    <Box
                      borderWidth="1px"
                      borderColor={
                        condition.isNot
                          ? "orange.300"
                          : isExpanded
                            ? "brand.200"
                            : "gray.200"
                      }
                      borderRadius="lg"
                      bg={
                        condition.isNot
                          ? "orange.50/30"
                          : isExpanded
                            ? "brand.50/10"
                            : "gray.50/50"
                      }
                      p={2}
                      transition="all 0.2s"
                    >
                      <Flex
                        justify="space-between"
                        align="center"
                        mb={isExpanded ? 1.5 : 0}
                      >
                        <Flex gap={2} align="center">
                          <Text
                            fontSize="sm"
                            fontWeight="bold"
                            color="gray.700"
                          >
                            Condition {index + 1}
                          </Text>
                          {!restriction && (
                            <Button
                              size="xs"
                              variant={condition.isNot ? "solid" : "outline"}
                              colorPalette={condition.isNot ? "orange" : "gray"}
                              onClick={() =>
                                handleUpdateCondition(index, {
                                  isNot: !condition.isNot,
                                })
                              }
                              disabled={isInitialSyncDone}
                              px={2}
                              h="20px"
                              fontSize="2xs"
                            >
                              {condition.isNot ? "NOT (Negated)" : "NOT"}
                            </Button>
                          )}
                        </Flex>
                        <Flex gap={1} align="center">
                          {isExpanded ? (
                            <CloseButton
                              size="xs"
                              onClick={() => toggleExpand(index)}
                              disabled={isFieldsLoading}
                            />
                          ) : (
                            <Button
                              size="xs"
                              variant="ghost"
                              onClick={() => toggleExpand(index)}
                              color="brand.600"
                              _hover={{ bg: "brand.50" }}
                            >
                              Show details
                            </Button>
                          )}
                          <IconButton
                            size="xs"
                            variant="ghost"
                            colorPalette="red"
                            aria-label="Delete condition"
                            onClick={() => handleRemoveCondition(index)}
                            disabled={isInitialSyncDone}
                          >
                            <IoMdTrash size={14} />
                          </IconButton>
                        </Flex>
                      </Flex>

                      {!isExpanded ? (
                        <Flex gap={1.5} mt={1} wrap="wrap" align="center">
                          {condition.isNot && (
                            <SummaryChip>
                              <Text
                                fontSize="xs"
                                color="orange.700"
                                fontWeight="bold"
                              >
                                NOT
                              </Text>
                            </SummaryChip>
                          )}
                          <SummaryChip>
                            <Text
                              fontSize="xs"
                              color="gray.700"
                              fontWeight="medium"
                            >
                              Column:{" "}
                              <strong>
                                {condition.column || "none"} (
                                {isFieldsLoading
                                  ? condition.edm_type
                                    ? toCamelCase(condition.edm_type)
                                    : "loading..."
                                  : getFieldDataType(
                                      tableFields[condition.column],
                                    )}
                                )
                              </strong>
                            </Text>
                          </SummaryChip>
                          <SummaryChip>
                            <Text
                              fontSize="xs"
                              color="gray.700"
                              fontWeight="medium"
                            >
                              {condition.mode === "range"
                                ? "Range"
                                : condition.mode === "multiple"
                                  ? "In"
                                  : operators.find(
                                      (op) => op.value === condition.operator,
                                    )?.label ||
                                    condition.operator ||
                                    "none"}
                            </Text>
                          </SummaryChip>
                          <SummaryChip>
                            <Text
                              fontSize="xs"
                              color="gray.700"
                              fontWeight="semibold"
                            >
                              {condition.mode === "range"
                                ? `"${condition.fromValue || ""}" to "${condition.toValue || ""}"`
                                : condition.mode === "multiple"
                                  ? `[${condition.multipleValues.join(", ")}]`
                                  : `"${condition.value}"`}
                            </Text>
                          </SummaryChip>
                        </Flex>
                      ) : (
                        <FilterConditionEditor
                          condition={condition}
                          fieldsList={fieldsList}
                          tableFields={tableFields}
                          onChange={(updated) =>
                            handleUpdateCondition(index, updated)
                          }
                          isFieldsLoading={isFieldsLoading}
                          disabled={isInitialSyncDone}
                        />
                      )}
                    </Box>
                  </Box>
                );
              })}

              {isFieldsLoading && conditions.length === 0 ? (
                <Flex direction="column" gap={2} py={3}>
                  <Skeleton height="28px" borderRadius="md" />
                  <Skeleton height="60px" borderRadius="md" />
                </Flex>
              ) : (
                <>
                  {!isFieldsLoading &&
                    fieldsList.length === 0 &&
                    conditions.length === 0 && (
                      <Text fontSize="xs" color="orange.500" mb={2}>
                        No filterable columns found. You may not be able to
                        create a valid filter.
                      </Text>
                    )}
                  <Button
                    size="xs"
                    variant="outline"
                    colorPalette="brand"
                    onClick={handleAddCondition}
                    mt={1}
                    alignSelf="flex-start"
                    gap={1}
                    disabled={isFieldsLoading || isInitialSyncDone}
                  >
                    <IoMdAdd /> Add condition
                  </Button>
                </>
              )}
            </Dialog.Body>

            <Dialog.Footer
              bg="gray.50"
              borderTopWidth="1px"
              borderColor="gray.200"
              p={2}
              display="flex"
              justifyContent="space-between"
              alignItems="center"
            >
              <Button
                size="xs"
                variant="ghost"
                colorPalette="red"
                onClick={handleClearAll}
                disabled={conditions.length === 0 || isInitialSyncDone}
              >
                Delete filter
              </Button>
              <Flex gap={2}>
                <Button size="xs" variant="outline" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  size="xs"
                  colorPalette="brand"
                  onClick={handleSave}
                  loading={isSaving}
                  disabled={isSaveDisabled || isInitialSyncDone}
                >
                  Save
                </Button>
              </Flex>
            </Dialog.Footer>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default RowFilterModal;
