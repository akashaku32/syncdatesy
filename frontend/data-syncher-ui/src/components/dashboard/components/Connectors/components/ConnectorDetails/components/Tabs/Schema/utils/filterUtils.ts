import {
  type FilterNode,
  type RowFilterConfig,
  type TableFieldInfo,
} from "@/types/connectors";

import { isPrimaryKey } from "../../ReverseSchema/utils/validation";

/**
 * Extracts all unique column names referenced in a RowFilterConfig structure.
 */
export const extractFilterColumns = (
  config: RowFilterConfig | null | undefined,
): string[] => {
  if (!config || !config.conditions) return [];
  const columns: string[] = [];

  const traverseNode = (node: FilterNode) => {
    if ("column" in node && node.column) {
      columns.push(node.column);
    } else if ("field" in node && node.field) {
      columns.push(node.field);
    } else if ("conditions" in node && Array.isArray(node.conditions)) {
      node.conditions.forEach(traverseNode);
    } else if ("not" in node && node.not) {
      traverseNode(node.not);
    }
  };

  config.conditions.forEach(traverseNode);
  return Array.from(new Set(columns));
};

/**
 * Identifies filter columns in a RowFilterConfig that are NOT primary keys.
 */
export const getNonKeyFilterColumns = (
  config: RowFilterConfig | null | undefined,
  tableFields?: Record<string, TableFieldInfo | string>,
  primaryKeys?: string[],
): string[] => {
  const columns = extractFilterColumns(config);
  return columns.filter((col) => {
    const info = tableFields ? tableFields[col] : undefined;
    const isPK = isPrimaryKey(
      col,
      typeof info === "string" ? null : (info ?? null),
      primaryKeys,
    );
    return !isPK;
  });
};

/**
 * Removes filter conditions targeting non-primary-key columns from a RowFilterConfig structure.
 * Returns null if no valid primary key conditions remain.
 */
export const cleanNonKeyRowFilterConditions = (
  config: RowFilterConfig | null | undefined,
  tableFields?: Record<string, TableFieldInfo | string>,
  primaryKeys?: string[],
): RowFilterConfig | null => {
  if (!config || !config.conditions || config.conditions.length === 0) {
    return null;
  }

  const cleanNode = (node: FilterNode): FilterNode | null => {
    if ("column" in node && node.column) {
      const info = tableFields ? tableFields[node.column] : undefined;
      const isPK = isPrimaryKey(
        node.column,
        typeof info === "string" ? null : (info ?? null),
        primaryKeys,
      );
      return isPK ? node : null;
    }
    if ("field" in node && node.field) {
      const info = tableFields ? tableFields[node.field] : undefined;
      const isPK = isPrimaryKey(
        node.field,
        typeof info === "string" ? null : (info ?? null),
        primaryKeys,
      );
      return isPK ? node : null;
    }
    if ("conditions" in node && Array.isArray(node.conditions)) {
      const validChildren = node.conditions
        .map(cleanNode)
        .filter((n): n is FilterNode => n !== null);
      if (validChildren.length === 0) return null;
      return { ...node, conditions: validChildren };
    }
    if ("not" in node && node.not) {
      const validChild = cleanNode(node.not);
      if (!validChild) return null;
      return { ...node, not: validChild };
    }
    return node;
  };

  const validConditions = config.conditions
    .map(cleanNode)
    .filter((n): n is FilterNode => n !== null);

  if (validConditions.length === 0) {
    return null;
  }

  return {
    ...config,
    conditions: validConditions,
  };
};

/**
 * Resolves effective RowFilterConfig falling back from row_filter_config to row_filter.
 */
export const getEffectiveRowFilter = (
  data?: {
    row_filter_config?: RowFilterConfig | null;
    row_filter?: RowFilterConfig | null;
  } | null,
): RowFilterConfig | null => {
  if (!data) return null;
  return data.row_filter_config || data.row_filter || null;
};

/**
 * Checks whether a RowFilterConfig contains active conditions.
 */
export const hasRowFilterConfig = (
  config?: RowFilterConfig | null,
): boolean => {
  return !!(config?.conditions && config.conditions.length > 0);
};

/**
 * Checks whether a table item has active row filter conditions in row_filter_config or row_filter.
 */
export const hasRowFilter = (
  item?: {
    row_filter_config?: RowFilterConfig | null;
    row_filter?: RowFilterConfig | null;
  } | null,
): boolean => {
  if (!item) return false;
  return (
    hasRowFilterConfig(item.row_filter_config) ||
    hasRowFilterConfig(item.row_filter)
  );
};
