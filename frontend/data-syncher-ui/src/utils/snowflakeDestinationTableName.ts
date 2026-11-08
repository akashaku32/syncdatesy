/** Snowflake destination table name normalisation (SAP OData → Snowflake). */

export type DestinationNameSource = "DEFAULT" | "JSON" | "MANUAL";

const SNOWFLAKE_RESERVED = new Set([
  "ACCOUNT",
  "ALL",
  "ALTER",
  "AND",
  "ANY",
  "AS",
  "BETWEEN",
  "BY",
  "CASE",
  "CAST",
  "CHECK",
  "COLUMN",
  "CONNECT",
  "CONNECTION",
  "CONSTRAINT",
  "CREATE",
  "CROSS",
  "CURRENT",
  "CURRENT_DATE",
  "CURRENT_TIME",
  "CURRENT_TIMESTAMP",
  "CURRENT_USER",
  "DATABASE",
  "DELETE",
  "DISTINCT",
  "DROP",
  "ELSE",
  "EXISTS",
  "FALSE",
  "FOLLOWING",
  "FOR",
  "FROM",
  "FULL",
  "GRANT",
  "GROUP",
  "GSCLUSTER",
  "HAVING",
  "ILIKE",
  "IN",
  "INCREMENT",
  "INNER",
  "INSERT",
  "INTERSECT",
  "INTO",
  "IS",
  "ISSUE",
  "JOIN",
  "LATERAL",
  "LEFT",
  "LIKE",
  "LOCALTIME",
  "LOCALTIMESTAMP",
  "MINUS",
  "NATURAL",
  "NOT",
  "NULL",
  "OF",
  "ON",
  "OR",
  "ORDER",
  "ORGANIZATION",
  "QUALIFY",
  "REGEXP",
  "REVOKE",
  "RIGHT",
  "RLIKE",
  "ROW",
  "ROWS",
  "SAMPLE",
  "SCHEMA",
  "SELECT",
  "SET",
  "SOME",
  "START",
  "TABLE",
  "TABLESAMPLE",
  "THEN",
  "TO",
  "TRIGGER",
  "TRUE",
  "TRY_CAST",
  "UNION",
  "UNIQUE",
  "UPDATE",
  "USING",
  "VALUES",
  "VIEW",
  "WHEN",
  "WHENEVER",
  "WHERE",
  "WITH",
]);

const IDENT_RE = /^[A-Z_][A-Z0-9_$]*$/;

/** Mirror of backend legacy ``normalize_name`` for default destination names. */
export function legacyNormalizeName(name: string): string {
  let result = name.replace(/__c/g, "_C");
  result = result.replace(/__+/g, "_");
  result = result.replace(/(?<=[a-z])([A-Z])/g, "_$1");
  result = result.replace(/(?<=[A-Za-z])(\d)/g, "_$1");
  result = result.replace(/(\d)(?=[A-Za-z])/g, "$1_");
  return result.toUpperCase();
}

export function extractJsonTargetObjectname(
  mappingJson: Record<string, unknown> | null | undefined,
): string | null {
  if (!mappingJson || typeof mappingJson !== "object") return null;
  const target = mappingJson.target;
  if (!target || typeof target !== "object") return null;
  const raw = (target as Record<string, unknown>).objectname;
  if (raw === undefined || raw === null) return null;
  const text = String(raw).trim();
  return text || null;
}

export function normalizeDestinationTableName(raw: string | null | undefined): {
  name: string;
  warnings: string[];
} {
  const warnings: string[] = [];
  if (raw === undefined || raw === null) return { name: "", warnings };
  let name = String(raw).trim();
  if (!name) return { name: "", warnings };

  name = name.replace(/[^A-Za-z0-9_$]/g, "_");
  name = name.replace(/_+/g, "_");
  name = name.replace(/^_+|_+$/g, "");
  if (!name) {
    warnings.push(
      "Table name normalised to an empty string; falling back to the default name.",
    );
    return { name: "", warnings };
  }

  if (/^\d/.test(name)) {
    name = `T_${name}`;
  }

  name = name.toUpperCase().slice(0, 255);

  if (SNOWFLAKE_RESERVED.has(name)) {
    name = `${name}_T`;
    warnings.push(`Name is a Snowflake reserved word; using '${name}' instead.`);
  }

  return { name, warnings };
}

export function isValidSnowflakeDestinationName(
  name: string | null | undefined,
): boolean {
  if (!name || !String(name).trim()) return false;
  const candidate = String(name).trim().toUpperCase();
  if (candidate.length > 255 || SNOWFLAKE_RESERVED.has(candidate)) return false;
  return IDENT_RE.test(candidate);
}

export function defaultDestinationDisplayName(
  serviceName: string,
  entityName: string,
): string {
  const service = (serviceName || "").trim();
  const entity = (entityName || "").trim();
  if (service && entity) return `${service}_${entity}`;
  return entity || service;
}

export function defaultDestinationTableName(
  serviceName: string,
  entityName: string,
  tableName: string,
): string {
  const base = (tableName || entityName || "").trim();
  if (!base) return legacyNormalizeName(defaultDestinationDisplayName(serviceName, entityName));
  return legacyNormalizeName(base);
}

export type ResolvedDestinationName = {
  jsonTableName: string | null;
  destinationTableName: string;
  destinationNameSource: DestinationNameSource;
  defaultDestinationTableName: string;
  defaultDisplayName: string;
  warnings: string[];
  valid: boolean;
};

export function resolveDestinationTableName(opts: {
  serviceName: string;
  entityName: string;
  tableName: string;
  mappingJson: Record<string, unknown> | null;
  destinationNameSource?: DestinationNameSource | null;
  manualDestinationTableName?: string | null;
}): ResolvedDestinationName {
  const defaultName = defaultDestinationTableName(
    opts.serviceName,
    opts.entityName,
    opts.tableName,
  );
  const defaultDisplay = defaultDestinationDisplayName(
    opts.serviceName,
    opts.entityName,
  );
  const warnings: string[] = [];
  const jsonRaw = extractJsonTargetObjectname(opts.mappingJson);

  let source = opts.destinationNameSource ?? null;
  if (!source) {
    if (opts.manualDestinationTableName?.trim()) {
      source = "MANUAL";
    } else if (jsonRaw) {
      source = "JSON";
    } else {
      source = "DEFAULT";
    }
  }

  if (source === "MANUAL") {
    const manual = (opts.manualDestinationTableName || "").trim();
    let candidate = manual.toUpperCase();
    if (!isValidSnowflakeDestinationName(candidate)) {
      const normalised = normalizeDestinationTableName(manual);
      warnings.push(...normalised.warnings);
      candidate = normalised.name;
    }
    if (!candidate || !isValidSnowflakeDestinationName(candidate)) {
      warnings.push("Manual destination table name is invalid.");
      return {
        jsonTableName: jsonRaw,
        destinationTableName: defaultName,
        destinationNameSource: "DEFAULT",
        defaultDestinationTableName: defaultName,
        defaultDisplayName: defaultDisplay,
        warnings,
        valid: false,
      };
    }
    return {
      jsonTableName: jsonRaw,
      destinationTableName: candidate,
      destinationNameSource: "MANUAL",
      defaultDestinationTableName: defaultName,
      defaultDisplayName: defaultDisplay,
      warnings,
      valid: true,
    };
  }

  if (source === "JSON" && jsonRaw) {
    const normalised = normalizeDestinationTableName(jsonRaw);
    warnings.push(...normalised.warnings);
    if (!normalised.name) {
      warnings.push(
        "JSON target.objectname normalised to empty; using default destination table name.",
      );
      return {
        jsonTableName: jsonRaw,
        destinationTableName: defaultName,
        destinationNameSource: "DEFAULT",
        defaultDestinationTableName: defaultName,
        defaultDisplayName: defaultDisplay,
        warnings,
        valid: Boolean(defaultName),
      };
    }
    return {
      jsonTableName: jsonRaw,
      destinationTableName: normalised.name,
      destinationNameSource: "JSON",
      defaultDestinationTableName: defaultName,
      defaultDisplayName: defaultDisplay,
      warnings,
      valid: true,
    };
  }

  return {
    jsonTableName: jsonRaw,
    destinationTableName: defaultName,
    destinationNameSource: "DEFAULT",
    defaultDestinationTableName: defaultName,
    defaultDisplayName: defaultDisplay,
    warnings,
    valid: Boolean(defaultName),
  };
}
