import { extractJsonTargetObjectname } from "@/utils/snowflakeDestinationTableName";

import { formatFieldType } from "./mappingState";

export const TRANSFORM_ENTRY_POINT = "transform";

export const RUN_ORDER_MAPPING_SCRIPT = "mapping_script";
export const RUN_ORDER_FILTER_SCRIPT_MAPPING = "filter_script_mapping";

/** Mapping JSON present: map first, then script. Otherwise the script runs on the SAP extract. */
export function deriveRunOrder(
  mappingJson: Record<string, unknown> | null | undefined,
): string {
  return mappingJson
    ? RUN_ORDER_MAPPING_SCRIPT
    : RUN_ORDER_FILTER_SCRIPT_MAPPING;
}

function scriptFileStem(raw: string): string {
  const cleaned = raw
    .trim()
    .replace(/[\\/:*?"<>|\s]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return cleaned || "transform";
}

/** Chip and editor title: JSON target object, otherwise the SAP entity name. */
export function defaultScriptFileName(
  mappingJson: Record<string, unknown> | null | undefined,
  entityName: string,
): string {
  const target = extractJsonTargetObjectname(mappingJson);
  const stem = scriptFileStem(target || entityName || "transform");
  return stem.toLowerCase().endsWith(".py") ? stem : `${stem}.py`;
}

export type TransformRootIssue = {
  message: string;
  line?: number;
};

/** The script must keep a single top-level function named transform. */
export function validateTransformRoot(
  script: string,
): TransformRootIssue | null {
  const lines = script.replace(/\r\n/g, "\n").split("\n");
  const defs: { name: string; line: number }[] = [];
  lines.forEach((line, index) => {
    const match = line.match(/^def\s+(\w+)\s*\(/);
    if (match) defs.push({ name: match[1], line: index + 1 });
  });
  const transforms = defs.filter((item) => item.name === TRANSFORM_ENTRY_POINT);
  if (transforms.length === 0) {
    const renamed = defs[0];
    return {
      message: renamed
        ? `Root function must be named transform (found ${renamed.name}).`
        : "Root function must be named transform.",
      line: renamed?.line,
    };
  }
  if (transforms.length > 1) {
    return {
      message: "Script must contain exactly one transform function.",
      line: transforms[1].line,
    };
  }
  const extra = defs.find((item) => item.name !== TRANSFORM_ENTRY_POINT);
  if (extra) {
    return {
      message: `Only transform may be a top-level function (found ${extra.name}).`,
      line: extra.line,
    };
  }
  return null;
}

const LEGACY_SHELL_HEADER = `import pandas as pd

def transform(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()`;

const LEGACY_SHELL_RETURN = "    return df";

export const TRANSFORM_SHELL_HEADER = `import pandas as pd

def transform(__source_df: pd.DataFrame) -> pd.DataFrame:
    __target_df = __source_df.copy()
    df = __target_df`;

export const TRANSFORM_SHELL_RETURN = "    return __target_df";

export const TRANSFORM_SHELL_HEADER_LINES =
  TRANSFORM_SHELL_HEADER.split("\n").length;

const PANDAS_IMPORT =
  /^(?:import\s+pandas(?:\s+as\s+\w+)?|from\s+pandas\s+import\s+.+)$/;

const DF_REASSIGN = /^df\s*=(?!=)/;

export type MappingFieldDetail = {
  name: string;
  typeLabel: string;
};

/** Column schema shown in the Script editor (__source_df / __target_df folds). */
export type ScriptSchemaField = {
  name: string;
  dtype: string;
};

export function mappingFieldDetails(
  mapping: Record<string, unknown> | null,
  side: "source" | "target",
): MappingFieldDetail[] {
  if (!mapping) return [];
  const section = mapping[side];
  if (!section || typeof section !== "object") return [];
  const fields = (section as { fields?: unknown }).fields;
  if (!Array.isArray(fields)) return [];
  return fields
    .map((field) => {
      if (!field || typeof field !== "object") return null;
      const meta = field as {
        fieldname?: unknown;
        datatype?: unknown;
        length?: unknown;
        decimals?: unknown;
      };
      const name =
        typeof meta.fieldname === "string" ? meta.fieldname.trim() : "";
      if (!name) return null;
      const datatype =
        typeof meta.datatype === "string" ? meta.datatype : undefined;
      const length = Number(meta.length || 0) || undefined;
      const decimals = Number(meta.decimals || 0) || undefined;
      return {
        name,
        typeLabel: formatFieldType(datatype, length, decimals),
      };
    })
    .filter((field): field is MappingFieldDetail => field !== null);
}

export function mappingFieldNames(
  mapping: Record<string, unknown> | null,
  side: "source" | "target",
): string[] {
  return mappingFieldDetails(mapping, side).map((field) => field.name);
}

const PANDAS_STRING_TYPES = new Set([
  "CHAR",
  "NUMC",
  "CLNT",
  "LANG",
  "UNIT",
  "CUKY",
  "DATS",
  "TIMS",
  "ACCP",
  "RAW",
  "LCHR",
  "SSTR",
  "STRG",
  "STRING",
  "VARCHAR",
  "TEXT",
]);

const PANDAS_FLOAT_TYPES = new Set([
  "CURR",
  "QUAN",
  "DEC",
  "FLTP",
  "DF34",
  "DF16",
  "NUMBER",
  "DECIMAL",
  "FLOAT",
  "DOUBLE",
]);

const PANDAS_INT_TYPES = new Set([
  "INT",
  "INT1",
  "INT2",
  "INT4",
  "INT8",
  "INTEGER",
  "SMALLINT",
  "BIGINT",
]);

const PANDAS_BOOL_TYPES = new Set(["BOOL", "BOOLEAN"]);

const PANDAS_DATETIME_TYPES = new Set([
  "DATE",
  "DATETIME",
  "TIMESTAMP",
  "TIMESTAMPLTZ",
  "TIMESTAMPNTZ",
  "TIMESTAMPTZ",
]);

const SAP_TYPE_LABEL =
  /^(CHAR|NUMC|CLNT|LANG|UNIT|CUKY|DATS|TIMS|CURR|QUAN|DEC|FLTP|INT\d?|RAW|BOOL)\s+\d/i;

/** Parse SAP display labels such as "CHAR 4" or "CURR 23,2" into pandas dtypes. */
export function formatSapLabelToPandasDtype(sapLabel: string): string {
  const trimmed = sapLabel.trim();
  if (!trimmed) return "object";
  const spaced = trimmed.match(/^([A-Za-z]+)\s+(\d+)(?:\s*,\s*(\d+))?$/);
  if (spaced) {
    return sapDatatypeToPandasDtype(
      spaced[1],
      spaced[3] ? Number(spaced[3]) : undefined,
    );
  }
  return sapDatatypeToPandasDtype(trimmed);
}

export function coerceScriptSchemaDtype(label: string): string {
  const trimmed = label.trim();
  if (!trimmed) return "object";
  if (SAP_TYPE_LABEL.test(trimmed)) {
    return formatSapLabelToPandasDtype(trimmed);
  }
  return trimmed;
}

function parseSapTypeToken(
  datatype?: string,
  _length?: number,
  decimals?: number,
): { datatype?: string; decimals?: number } {
  if (!datatype?.trim()) {
    return { datatype: undefined, decimals };
  }
  const raw = datatype.trim();
  const spaced = raw.match(/^([A-Za-z]+)\s+(\d+)(?:\s*,\s*(\d+))?$/);
  if (spaced) {
    return {
      datatype: spaced[1],
      decimals: spaced[3] ? Number(spaced[3]) : decimals,
    };
  }
  const paren = raw.match(/^([A-Za-z]+)\s*\(\s*(\d+)(?:\s*,\s*(\d+))?\s*\)$/i);
  if (paren) {
    return {
      datatype: paren[1],
      decimals: paren[3] ? Number(paren[3]) : decimals,
    };
  }
  if (raw.toLowerCase().startsWith("edm.")) {
    const edm = raw.slice(4).toLowerCase();
    if (edm.includes("string")) return { datatype: "CHAR", decimals };
    if (edm.includes("decimal") || edm.includes("double")) {
      return { datatype: "DEC", decimals };
    }
    if (edm.includes("int")) return { datatype: "INT4", decimals };
    if (edm.includes("bool")) return { datatype: "BOOL", decimals };
    if (edm.includes("date") || edm.includes("time")) {
      return { datatype: "DATETIME", decimals };
    }
  }
  return { datatype: raw.split(/\s+/)[0], decimals };
}

/** Typical pandas dtype for a column in transform(__source_df) / __target_df. */
export function sapDatatypeToPandasDtype(
  datatype?: string,
  decimals?: number,
): string {
  if (!datatype) return "object";
  const parsed = parseSapTypeToken(datatype, undefined, decimals);
  if (!parsed.datatype) return "object";
  const normalized = parsed.datatype.toUpperCase().trim();
  const scale = parsed.decimals;
  if (PANDAS_BOOL_TYPES.has(normalized)) return "bool";
  if (PANDAS_DATETIME_TYPES.has(normalized)) return "datetime64[ns]";
  if (PANDAS_STRING_TYPES.has(normalized)) return "string";
  if (PANDAS_FLOAT_TYPES.has(normalized)) return "float64";
  if (PANDAS_INT_TYPES.has(normalized) && !scale) return "Int64";
  if (scale) return "float64";
  return "object";
}

type JsonFieldMeta = {
  datatype?: string;
  length?: number;
  decimals?: number;
};

function jsonFieldMetaByName(
  mapping: Record<string, unknown> | null,
  side: "source" | "target",
): Map<string, JsonFieldMeta> {
  const map = new Map<string, JsonFieldMeta>();
  if (!mapping) return map;
  const section = mapping[side];
  if (!section || typeof section !== "object") return map;
  const fields = (section as { fields?: unknown }).fields;
  if (!Array.isArray(fields)) return map;
  for (const field of fields) {
    if (!field || typeof field !== "object") continue;
    const meta = field as {
      fieldname?: unknown;
      datatype?: unknown;
      data_type?: unknown;
      type?: unknown;
      length?: unknown;
      decimals?: unknown;
    };
    const name =
      typeof meta.fieldname === "string" ? meta.fieldname.trim() : "";
    if (!name) continue;
    const datatype =
      typeof meta.datatype === "string"
        ? meta.datatype
        : typeof meta.data_type === "string"
          ? meta.data_type
          : typeof meta.type === "string"
            ? meta.type
            : undefined;
    const length = Number(meta.length || 0) || undefined;
    const decimals = Number(meta.decimals || 0) || undefined;
    map.set(name.toUpperCase(), { datatype, length, decimals });
  }
  return map;
}

function schemaFieldPandasDtype(
  side: "source" | "target",
  name: string,
  row: { source: string | null; target: string },
  jsonMeta: Map<string, JsonFieldMeta>,
  sapByName: Map<string, { type?: string; length?: number; decimals?: number }>,
): string {
  const key = name.toUpperCase();
  const json = jsonMeta.get(key);
  const sap =
    side === "source"
      ? sapByName.get(key)
      : row.source
        ? sapByName.get(row.source.toUpperCase())
        : undefined;
  const datatype = json?.datatype || sap?.type;
  const decimals = json?.decimals !== undefined ? json.decimals : sap?.decimals;
  const length = json?.length !== undefined ? json.length : sap?.length;
  const parsed = parseSapTypeToken(datatype, length, decimals);
  return sapDatatypeToPandasDtype(parsed.datatype, parsed.decimals);
}

function schemaFieldsFromMappingRows(
  side: "source" | "target",
  rows: { source: string | null; target: string }[],
  mapping: Record<string, unknown> | null,
  sapColumns: {
    name: string;
    type?: string;
    length?: number;
    decimals?: number;
  }[],
): ScriptSchemaField[] {
  const jsonMeta = jsonFieldMetaByName(mapping, side);
  const sapByName = new Map(
    sapColumns.map((column) => [column.name.toUpperCase(), column]),
  );
  const seen = new Set<string>();
  const fields: ScriptSchemaField[] = [];
  for (const row of rows) {
    const name =
      side === "target" ? row.target.trim() : (row.source?.trim() ?? "");
    if (!name) continue;
    const key = name.toUpperCase();
    if (seen.has(key)) continue;
    seen.add(key);
    fields.push({
      name,
      dtype: schemaFieldPandasDtype(side, name, row, jsonMeta, sapByName),
    });
  }
  return fields;
}

/** Columns passed into transform (mapped SAP sources from step 1). */
export function inputSchemaFields(
  rows: { source: string | null; target: string }[],
  mapping: Record<string, unknown> | null,
  sapColumns: {
    name: string;
    type?: string;
    length?: number;
    decimals?: number;
  }[],
): ScriptSchemaField[] {
  return schemaFieldsFromMappingRows("source", rows, mapping, sapColumns);
}

/** Columns returned from transform (effective target mapping from JSON + step 1). */
export function outputSchemaFields(
  rows: { source: string | null; target: string }[],
  mapping: Record<string, unknown> | null,
  sapColumns: {
    name: string;
    type?: string;
    length?: number;
    decimals?: number;
  }[],
): ScriptSchemaField[] {
  return schemaFieldsFromMappingRows("target", rows, mapping, sapColumns);
}

function indentBody(body: string): string {
  const normalized = body.replace(/\r\n/g, "\n").replace(/\s+$/, "");
  if (!normalized) return "";
  return normalized
    .split("\n")
    .map((line) => (line.trim() === "" ? "" : `    ${line}`))
    .join("\n");
}

function dedentLine(line: string): string {
  if (line.startsWith("    ")) return line.slice(4);
  if (line.startsWith("\t")) return line.slice(1);
  return line;
}

function dedentBody(raw: string): string {
  const lines = raw.replace(/\r\n/g, "\n").split("\n").map(dedentLine);
  while (lines.length > 0 && lines[0].trim() === "") lines.shift();
  while (lines.length > 0 && lines[lines.length - 1].trim() === "") lines.pop();
  return lines.join("\n");
}

export function wrapTransformBody(body: string): string {
  const indented = indentBody(body);
  const middle = indented ? `\n${indented}\n` : "\n";
  return `${TRANSFORM_SHELL_HEADER}${middle}\n${TRANSFORM_SHELL_RETURN}\n`;
}

function isPandasImport(line: string): boolean {
  return PANDAS_IMPORT.test(line.trim());
}

function topLevelDefs(script: string): number {
  return script.split("\n").filter((line) => /^def\s+\w+\s*\(/.test(line))
    .length;
}

function extractSingleFunction(script: string): string | null {
  if (topLevelDefs(script) !== 1) return null;
  const lines = script.split("\n");
  const defIndex = lines.findIndex((line) => /^def\s+\w+\s*\(/.test(line));
  if (defIndex < 0) return null;

  const preamble = lines.slice(0, defIndex).filter((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return false;
    return !isPandasImport(trimmed);
  });

  const body: string[] = [];
  for (const line of lines.slice(defIndex + 1)) {
    if (line.trim() === "") {
      body.push("");
      continue;
    }
    if (line.startsWith(" ") || line.startsWith("\t")) {
      body.push(dedentLine(line));
      continue;
    }
    break;
  }

  const combined = [...preamble, ...body];
  while (combined.length > 0 && combined[0].trim() === "") combined.shift();
  while (combined.length > 0 && combined[combined.length - 1].trim() === "")
    combined.pop();
  return combined.join("\n");
}

function unwrapLockedShell(
  script: string,
  header: string,
  footer: string,
): string | null {
  if (!script.startsWith(header)) return null;
  let rest = script.slice(header.length);
  if (rest.startsWith("\n")) rest = rest.slice(1);
  if (!rest.endsWith(footer)) return null;
  const middle = rest.slice(0, rest.length - footer.length).replace(/\n+$/, "");
  return dedentBody(middle);
}

function bodyReassignsDf(body: string): boolean {
  return body.split("\n").some((line) => DF_REASSIGN.test(line.trim()));
}

function withLegacyAlias(body: string): string {
  if (!bodyReassignsDf(body)) return body;
  const trimmed = body.replace(/\s+$/, "");
  if (/(^|\n)__target_df\s*=\s*df\s*$/.test(trimmed)) return trimmed;
  return trimmed ? `${trimmed}\n__target_df = df` : "__target_df = df";
}

export function unwrapTransformBody(script: string): string {
  const normalized = script.replace(/\r\n/g, "\n").trim();
  if (!normalized) return "";
  const current = unwrapLockedShell(
    normalized,
    TRANSFORM_SHELL_HEADER,
    TRANSFORM_SHELL_RETURN,
  );
  if (current !== null) return current;
  const legacy = unwrapLockedShell(
    normalized,
    LEGACY_SHELL_HEADER,
    LEGACY_SHELL_RETURN,
  );
  if (legacy !== null) return withLegacyAlias(legacy);
  const extracted = extractSingleFunction(normalized);
  if (extracted !== null) return extracted;
  return normalized;
}

export function normalizeTransformScript(script: string): string {
  if (!script.trim()) return "";
  return wrapTransformBody(unwrapTransformBody(script));
}

/** Empty when the toggle is off or the locked shell has no user body. */
export function persistedScriptText(
  applyScript: boolean,
  scriptText: string,
): string {
  if (!applyScript) return "";
  if (!unwrapTransformBody(scriptText).trim()) return "";
  return scriptText;
}

export function scriptBodyLine(scriptLine?: number): number | undefined {
  if (!scriptLine || scriptLine <= 0) return undefined;
  if (scriptLine <= TRANSFORM_SHELL_HEADER_LINES) return scriptLine;
  return scriptLine - TRANSFORM_SHELL_HEADER_LINES;
}

/** Line in the user script from a sandbox traceback, otherwise the reported line. */
export function scriptErrorLine(
  traceback: string | undefined,
  fallback?: number,
): number | undefined {
  if (traceback) {
    const pattern = /File "<transform_script>", line (\d+)/g;
    let line: number | undefined;
    for (const match of traceback.matchAll(pattern)) {
      const parsed = Number(match[1]);
      if (parsed > 0) line = parsed;
    }
    if (line) return line;
  }
  return fallback && fallback > 0 ? fallback : undefined;
}
