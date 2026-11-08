import {
  type ExcelConditionItem,
  type ExcelConditionalFormat,
  type ExcelDifferentialStyle,
} from "@/types/connectors";

import {
  type ConditionRuleItem,
  type ConditionRuleNode,
  conditionsToTree,
  nodeToHumanSummary,
} from "./groupModelUtils";
import { type ColumnDataType } from "./types";

export const getColumnDataType = (
  typeStr: string | undefined,
): ColumnDataType => {
  if (!typeStr) return "text";
  const lower = typeStr.toLowerCase();
  if (
    lower.includes("number") ||
    lower.includes("int") ||
    lower.includes("float") ||
    lower.includes("double") ||
    lower.includes("decimal") ||
    lower.includes("numeric") ||
    lower.includes("real")
  ) {
    return "numeric";
  }
  if (
    lower.includes("date") ||
    lower.includes("time") ||
    lower.includes("timestamp")
  ) {
    return "datetime";
  }
  if (lower.includes("bool")) {
    return "boolean";
  }
  return "text";
};

export const stripFormulaQuotes = (val?: string): string => {
  if (!val) return "";
  const trimmed = String(val).trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/""/g, '"');
  }
  return val;
};

export const formatCellIsFormulaValue = (
  val: string,
  colDataType?: ColumnDataType,
): string => {
  if (val === undefined || val === null) return "";
  const trimmed = String(val).trim();
  if (trimmed === "") return "";

  // If it's an Excel formula starting with '=', keep as formula
  if (trimmed.startsWith("=")) {
    return trimmed;
  }

  // If it's a boolean
  if (
    colDataType === "boolean" ||
    trimmed.toUpperCase() === "TRUE" ||
    trimmed.toUpperCase() === "FALSE"
  ) {
    return trimmed.toUpperCase();
  }

  // If it's a valid number without spaces (e.g. 10, -5, 3.14)
  const isNum =
    colDataType === "numeric"
      ? !isNaN(Number(trimmed))
      : !isNaN(Number(trimmed)) && !trimmed.includes(" ");

  if (isNum) {
    return trimmed;
  }

  // If it is already quoted in double quotes (e.g. '"Keerthi Ramesh"')
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
    return trimmed;
  }

  // If it is quoted in single quotes (e.g. "'Keerthi Ramesh'") -> convert to double quotes
  if (trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2) {
    const unquoted = trimmed.slice(1, -1);
    return `"${unquoted.replace(/"/g, '""')}"`;
  }

  // Otherwise, it is a text string literal -> wrap in double quotes, escaping any inner quotes
  return `"${trimmed.replace(/"/g, '""')}"`;
};

export const getValidationError = (
  value: string | string[] | undefined,
  colDataType: ColumnDataType,
): string | null => {
  if (value === undefined || value === null) return null;
  const rawStr = Array.isArray(value) ? value[0] : value;
  if (!rawStr || String(rawStr).trim() === "") return null;
  const valStr = stripFormulaQuotes(String(rawStr).trim());
  if (valStr.startsWith("=")) return null;

  if (colDataType === "numeric") {
    const num = Number(valStr);
    if (isNaN(num)) {
      return "Value must be a number or a formula starting with '='";
    }
  }

  if (colDataType === "datetime") {
    const timestamp = Date.parse(valStr);
    const isIsoDate = /^\d{4}-\d{2}-\d{2}$/.test(valStr);
    if (isNaN(timestamp) && !isIsoDate) {
      return "Value must be a valid date or a formula starting with '='";
    }
  }

  return null;
};

export const getColumnLetter = (index: number): string => {
  let letter = "";
  let temp = index;
  while (temp >= 0) {
    letter = String.fromCharCode((temp % 26) + 65) + letter;
    temp = Math.floor(temp / 26) - 1;
  }
  return letter;
};

const formatExcelClause = (
  c: ConditionRuleItem,
  colNames?: string[],
  tableFields?: Record<string, string>,
): string => {
  const col = c.column ?? "";
  if (!col) return "";
  const colIndex = colNames ? colNames.indexOf(col) : -1;
  const colRef = colIndex >= 0 ? `$${getColumnLetter(colIndex)}2` : `$${col}2`;
  const val = (c.value ?? "").trim();
  if (val.startsWith("=")) {
    return `${colRef}=${val.slice(1)}`;
  }

  const rawType = tableFields ? tableFields[col] : undefined;
  const dataType = getColumnDataType(rawType);
  const isBool =
    dataType === "boolean" ||
    val.toUpperCase() === "TRUE" ||
    val.toUpperCase() === "FALSE";
  const isNum =
    dataType === "numeric"
      ? !isNaN(Number(val)) && val !== ""
      : !isNaN(Number(val)) && val !== "" && !val.includes(" ");

  const formattedVal = isBool
    ? val.toUpperCase()
    : isNum
      ? val
      : `"${val.replace(/"/g, '""')}"`;

  let clause = "";
  switch (c.operator) {
    case "notEqual":
      clause = `${colRef}<>${formattedVal}`;
      break;
    case "greaterThan":
      clause = `${colRef}>${formattedVal}`;
      break;
    case "lessThan":
      clause = `${colRef}<${formattedVal}`;
      break;
    case "greaterThanOrEqual":
      clause = `${colRef}>=${formattedVal}`;
      break;
    case "lessThanOrEqual":
      clause = `${colRef}<=${formattedVal}`;
      break;
    case "contains":
      clause = `ISNUMBER(SEARCH(${formattedVal},${colRef}))`;
      break;
    case "notContains":
      clause = `ISERROR(SEARCH(${formattedVal},${colRef}))`;
      break;
    case "beginsWith":
      clause = `LEFT(${colRef},LEN(${formattedVal}))=${formattedVal}`;
      break;
    case "notBeginsWith":
      clause = `LEFT(${colRef},LEN(${formattedVal}))<>${formattedVal}`;
      break;
    case "endsWith":
      clause = `RIGHT(${colRef},LEN(${formattedVal}))=${formattedVal}`;
      break;
    case "notEndsWith":
      clause = `RIGHT(${colRef},LEN(${formattedVal}))<>${formattedVal}`;
      break;
    case "isEmpty":
      clause = `ISBLANK(${colRef})`;
      break;
    case "isNotEmpty":
      clause = `NOT(ISBLANK(${colRef}))`;
      break;
    case "between": {
      const val2 = (c.value2 ?? "").trim();
      const isNum2 = !isNaN(Number(val2)) && val2 !== "";
      const formattedVal2 = isNum2 ? val2 : `"${val2.replace(/"/g, '""')}"`;
      clause = `AND(${colRef}>=${formattedVal},${colRef}<=${formattedVal2})`;
      break;
    }
    case "notBetween": {
      const val2 = (c.value2 ?? "").trim();
      const isNum2 = !isNaN(Number(val2)) && val2 !== "";
      const formattedVal2 = isNum2 ? val2 : `"${val2.replace(/"/g, '""')}"`;
      clause = `OR(${colRef}<${formattedVal},${colRef}>${formattedVal2})`;
      break;
    }
    case "equal":
    default:
      clause = `${colRef}=${formattedVal}`;
      break;
  }

  return clause;
};

const formatNodeFormula = (
  node: ConditionRuleNode,
  colNames?: string[],
  tableFields?: Record<string, string>,
): string => {
  if (node.type === "rule") {
    return formatExcelClause(node, colNames, tableFields);
  }
  const parts = node.children
    .map((child) => formatNodeFormula(child, colNames, tableFields))
    .filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0];
  const fn = node.conjunction.toUpperCase();
  return `${fn}(${parts.join(",")})`;
};

// Generates valid Excel OpenXML Formula
export const generateMultipleConditionsFormula = (
  conditions: ExcelConditionItem[],
  colNames?: string[],
  tableFields?: Record<string, string>,
): string => {
  if (!conditions || conditions.length === 0) return "";
  const valid = conditions.filter((c) => c.column);
  if (valid.length === 0) return "";
  const tree = conditionsToTree(valid);
  const formulaBody = formatNodeFormula(tree, colNames, tableFields);
  return formulaBody ? `=${formulaBody}` : "";
};

// Generates human-friendly preview directly from tree
export const formatHumanSummary = (
  conditions: ExcelConditionItem[],
  _targetColumns?: string[],
  _highlightScope?: string,
  tableFields?: Record<string, string>,
): string => {
  const valid = (conditions || []).filter((c) => c.column);
  if (valid.length === 0) return "";
  const tree = conditionsToTree(valid);
  return nodeToHumanSummary(tree, tableFields, true);
};

export const getCssColor = (
  hex: string | undefined,
  defaultColor: string,
): string => {
  if (!hex) return defaultColor;
  const clean = hex.trim();
  if (clean.startsWith("#")) return clean;
  if (/^[0-9A-Fa-f]{3,6}$/.test(clean)) return `#${clean}`;
  return clean;
};

export const getCssAlignItems = (val?: string) => {
  if (val === "top") return "flex-start";
  if (val === "bottom") return "flex-end";
  return "center";
};

export const normalizeRuleType = (type: string | undefined): string => {
  if (!type) return "";
  return type.toLowerCase().replace(/[^a-z0-9]/g, "");
};

export const isRuleTypeAllowed = (
  ruleType: string,
  colDataType: ColumnDataType,
): boolean => {
  const norm = ruleType.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (norm === "multipleconditions") {
    return true;
  }
  if (colDataType === "boolean") {
    return ["cellis", "formula"].includes(norm);
  }
  if (colDataType === "text") {
    return [
      "cellis",
      "containstext",
      "notcontainstext",
      "beginswith",
      "endswith",
      "duplicaterecords",
      "uniquerecords",
      "formula",
    ].includes(norm);
  }
  if (colDataType === "datetime") {
    return ["cellis", "formula", "duplicaterecords", "uniquerecords"].includes(
      norm,
    );
  }
  return ![
    "containstext",
    "notcontainstext",
    "beginswith",
    "endswith",
  ].includes(norm);
};

export const isOperatorAllowed = (
  operator: string,
  colDataType: ColumnDataType,
): boolean => {
  if (colDataType === "boolean" || colDataType === "text") {
    return ["equal", "notEqual"].includes(operator);
  }
  return true;
};

export const validateConditionalFormatRules = (
  rules: ExcelConditionalFormat[],
  tableFields?: Record<string, string>,
): string | null => {
  for (let i = 0; i < rules.length; i++) {
    const rule = rules[i];
    const ruleNum = i + 1;
    const typeNorm = normalizeRuleType(rule.type);

    if (typeNorm === "multipleconditions") {
      if (!rule.conditions || rule.conditions.length === 0) {
        return `Rule #${ruleNum}: Please add at least one condition.`;
      }
      for (let j = 0; j < rule.conditions.length; j++) {
        const c = rule.conditions[j];
        if (!c.column || c.column.trim() === "") {
          return `Rule #${ruleNum}: Please select a column for all conditions.`;
        }
        const op = c.operator || "equal";
        if (op === "isEmpty" || op === "isNotEmpty") {
          continue;
        }
        if (op === "between" || op === "notBetween") {
          if (!c.value || c.value.trim() === "") {
            return `Rule #${ruleNum}: Please enter a minimum value for "${c.column}".`;
          }
          if (!c.value2 || c.value2.trim() === "") {
            return `Rule #${ruleNum}: Please enter a maximum value for "${c.column}".`;
          }
        } else {
          if (
            c.value === undefined ||
            c.value === null ||
            c.value.trim() === ""
          ) {
            return `Rule #${ruleNum}: Please enter a value for "${c.column}".`;
          }
        }
        const colRawType = tableFields?.[c.column];
        const colDataType = getColumnDataType(colRawType);
        const err1 = getValidationError(c.value, colDataType);
        if (err1) {
          return `Rule #${ruleNum} ("${c.column}"): ${err1}`;
        }
        if (op === "between" || op === "notBetween") {
          const err2 = getValidationError(c.value2, colDataType);
          if (err2) {
            return `Rule #${ruleNum} ("${c.column}"): ${err2}`;
          }
        }
      }
    } else if (typeNorm === "cellis") {
      const col = rule.condition_column_name || rule.column_name;
      if (!col || col.trim() === "") {
        return `Rule #${ruleNum}: Please select a column to evaluate.`;
      }
      const op = rule.operator || "equal";
      if (op === "between" || op === "notBetween") {
        const val1 = Array.isArray(rule.formula)
          ? rule.formula[0]
          : rule.formula;
        const val2 = Array.isArray(rule.formula) ? rule.formula[1] : undefined;
        if (!val1 || val1.trim() === "") {
          return `Rule #${ruleNum}: Please enter a minimum value for "${col}".`;
        }
        if (!val2 || val2.trim() === "") {
          return `Rule #${ruleNum}: Please enter a maximum value for "${col}".`;
        }
      } else {
        const val = Array.isArray(rule.formula)
          ? rule.formula[0]
          : rule.formula;
        if (!val || val.trim() === "") {
          return `Rule #${ruleNum}: Please enter a value for "${col}".`;
        }
      }
    } else if (
      ["containstext", "notcontainstext", "beginswith", "endswith"].includes(
        typeNorm,
      )
    ) {
      if (!rule.text || rule.text.trim() === "") {
        return `Rule #${ruleNum}: Please enter text to match.`;
      }
    } else if (["formula", "expression"].includes(typeNorm)) {
      const formulaVal = Array.isArray(rule.formula)
        ? rule.formula[0]
        : rule.formula;
      if (!formulaVal || formulaVal.trim() === "") {
        return `Rule #${ruleNum}: Please enter a custom formula.`;
      }
    }
  }
  return null;
};

export const cleanRulePayload = (
  rule: ExcelConditionalFormat,
  colNames?: string[] | unknown,
  tableFields?: Record<string, string> | unknown,
): ExcelConditionalFormat => {
  const safeColNames = Array.isArray(colNames) ? colNames : undefined;
  const safeTableFields =
    tableFields &&
    typeof tableFields === "object" &&
    !Array.isArray(tableFields)
      ? (tableFields as Record<string, string>)
      : undefined;
  const typeNorm = normalizeRuleType(rule.type);

  const base: ExcelConditionalFormat = {
    type: typeNorm,
  };

  const colVal =
    rule.target_columns && rule.target_columns.length > 0
      ? rule.target_columns.join(",")
      : rule.column_name || "";

  if (rule.range !== undefined && rule.range !== null && rule.range !== "") {
    base.range = rule.range;
  } else {
    base.column_name = colVal;
  }

  if (rule.target_columns && rule.target_columns.length > 0) {
    base.target_columns = rule.target_columns;
  }

  if (rule.condition_column_name) {
    base.condition_column_name = rule.condition_column_name;
  }

  if (rule.stop_if_true !== undefined) {
    base.stop_if_true = rule.stop_if_true;
  }

  base.highlight_scope = rule.highlight_scope || "cell";

  const cleanStyle = (
    s: ExcelDifferentialStyle | undefined,
  ): ExcelDifferentialStyle | undefined => {
    if (!s) return undefined;

    const hasFill = !!(s.fill || s.fill_color || s.background_color);
    const hasFont = !!s.font_color;
    const hasBold = s.bold !== undefined;
    const hasItalic = s.italic !== undefined;

    if (!hasFill && !hasFont && !hasBold && !hasItalic) {
      return undefined;
    }

    const cleaned: ExcelDifferentialStyle = {};
    if (s.bold !== undefined) cleaned.bold = s.bold;
    if (s.italic !== undefined) cleaned.italic = s.italic;
    if (s.horizontal !== undefined) cleaned.horizontal = s.horizontal;
    if (s.vertical !== undefined) cleaned.vertical = s.vertical;
    if (s.wrap_text !== undefined) cleaned.wrap_text = s.wrap_text;

    const fillVal = s.fill || s.fill_color || s.background_color;
    if (fillVal) {
      const hex = fillVal.replace("#", "");
      cleaned.fill = hex;
      cleaned.fill_color = hex;
      cleaned.background_color = hex;
    }
    if (s.font_color) {
      cleaned.font_color = s.font_color.replace("#", "");
    }
    return cleaned;
  };

  switch (typeNorm) {
    case "cellis": {
      base.operator = rule.operator || "equal";
      const evalCol = rule.condition_column_name || rule.column_name || "";
      const rawType = safeTableFields ? safeTableFields[evalCol] : undefined;
      const colDataType = getColumnDataType(rawType);
      const rawFormulas = Array.isArray(rule.formula)
        ? rule.formula.filter((f) => f !== "" && f !== undefined && f !== null)
        : rule.formula !== undefined &&
            rule.formula !== null &&
            rule.formula !== ""
          ? [rule.formula]
          : [];
      base.formula = rawFormulas.map((f) =>
        formatCellIsFormulaValue(String(f), colDataType),
      );
      base.style = cleanStyle(rule.style);
      break;
    }

    case "formula":
    case "expression": {
      const rawFormulas = Array.isArray(rule.formula)
        ? rule.formula.filter((f) => f !== "" && f !== undefined && f !== null)
        : rule.formula !== undefined &&
            rule.formula !== null &&
            rule.formula !== ""
          ? [rule.formula]
          : [];
      base.formula = rawFormulas.map((f) => {
        const trimmed = String(f).trim();
        if (!trimmed) return "";
        return trimmed.startsWith("=") ? trimmed : `=${trimmed}`;
      });
      base.style = cleanStyle(rule.style);
      break;
    }

    case "containstext":
    case "notcontainstext":
    case "beginswith":
    case "endswith":
      if (rule.text !== undefined) {
        base.text = rule.text;
      }
      if (rule.formula) {
        base.formula = Array.isArray(rule.formula)
          ? rule.formula.filter((f) => f !== "")
          : [rule.formula];
      }
      base.style = cleanStyle(rule.style);
      break;

    case "duplicaterecords":
    case "uniquerecords":
      base.style = cleanStyle(rule.style);
      break;

    case "aboveaverage":
      if (rule.above_average !== undefined)
        base.above_average = rule.above_average;
      if (rule.below_average !== undefined)
        base.below_average = rule.below_average;
      if (rule.equal_average !== undefined)
        base.equal_average = rule.equal_average;
      base.style = cleanStyle(rule.style);
      break;

    case "top10":
      if (rule.rank !== undefined) base.rank = rule.rank;
      if (rule.percent !== undefined) base.percent = rule.percent;
      if (rule.bottom !== undefined) base.bottom = rule.bottom;
      base.style = cleanStyle(rule.style);
      break;

    case "timeperiod":
      base.time_period = rule.time_period || "today";
      base.style = cleanStyle(rule.style);
      break;

    case "colorscale": {
      const rawColors = rule.colors || [];
      const minColor = (rawColors[0] || "").replace("#", "").trim();
      const midColor = (rawColors[1] || "").replace("#", "").trim();
      const maxColor = (rawColors[2] || "").replace("#", "").trim();

      const list: string[] = [];
      list.push(minColor || "F8696B");
      if (midColor) {
        list.push(midColor);
      }
      list.push(maxColor || "63BE7B");

      base.colors = list;
      break;
    }

    case "databar":
      if (rule.color) {
        base.color = rule.color.replace("#", "");
      }
      if (rule.show_value !== undefined) base.show_value = rule.show_value;
      if (rule.start_type !== undefined) base.start_type = rule.start_type;
      if (rule.start_value !== undefined) base.start_value = rule.start_value;
      if (rule.end_type !== undefined) base.end_type = rule.end_type;
      if (rule.end_value !== undefined) base.end_value = rule.end_value;
      break;

    case "iconset":
      base.icon_style = rule.icon_style || "3TrafficLights1";
      if (rule.show_value !== undefined) base.show_value = rule.show_value;
      if (rule.percent !== undefined) base.percent = rule.percent;
      if (rule.value_type !== undefined) base.value_type = rule.value_type;
      if (rule.values !== undefined) base.values = rule.values;
      if (rule.reverse !== undefined) base.reverse = rule.reverse;
      break;

    case "multipleconditions": {
      base.type = "multipleconditions";
      base.highlight_scope = rule.highlight_scope || "entire_row";
      base.target_columns = Array.isArray(rule.target_columns)
        ? rule.target_columns
        : [];
      base.column_name = base.target_columns[0] || "";
      const cleanedConds = (rule.conditions || []).map((c, i) => ({
        ...(i > 0
          ? {
              logic: c.logic === "or" ? ("or" as const) : ("and" as const),
            }
          : {}),
        column: c.column || "",
        operator: c.operator || "equal",
        value: c.value ?? "",
        ...(c.value2 !== undefined ? { value2: c.value2 } : {}),
        ...(c.open_paren ? { open_paren: c.open_paren } : {}),
        ...(c.close_paren ? { close_paren: c.close_paren } : {}),
      }));
      base.conditions = cleanedConds;
      const validForFormula = cleanedConds.filter((c) => c.column);
      base.formula = generateMultipleConditionsFormula(
        validForFormula,
        safeColNames,
        safeTableFields,
      );
      base.style = cleanStyle(rule.style);
      break;
    }

    default:
      base.style = cleanStyle(rule.style);
      break;
  }

  return base;
};
