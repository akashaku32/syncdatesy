import { isValidSnowflakeDestinationName } from "@/utils/snowflakeDestinationTableName";

export type MappingOrigin = "JSON" | "USER" | "IDENTITY";

export type SapColumn = {
  name: string;
  type: string;
  length?: number;
  decimals?: number;
  isKey?: boolean;
};

export type FieldMeta = {
  fieldname: string;
  datatype?: string;
  length?: number;
  decimals?: number;
  isKey?: boolean;
};

export type SavedMappingOverride = {
  id: string;
  kind: "repoint" | "rename" | "added";
  source: string | null;
  target: string;
  jsonTarget?: string | null;
};

export type OverrideSnapshot = {
  addedSources: string[];
  removedTargets: string[];
  removedJsonMappings: string[];
  overrideMappings: SavedMappingOverride[];
  /** Mapped-column order by target name. Empty keeps the JSON order. */
  targetOrder?: string[];
};

export type MappingJsonFile = {
  fileName: string;
  size: number;
  raw: Record<string, unknown>;
  sourceObject: string | null;
  tableNameRaw: string | null;
};

export type MappingDraft = {
  json: MappingJsonFile | null;
  sapColumns: SapColumn[];
  addedSources: string[];
  removedTargets: string[];
  removedJsonMappings: string[];
  overrideMappings: SavedMappingOverride[];
  targetOrder?: string[];
};

export type MappingRow = {
  id: string;
  source: string | null;
  target: string;
  origin: MappingOrigin;
  targetLocked: boolean;
  overridden: boolean;
  status: string;
  isKey?: boolean;
};

export type MappingCounters = {
  mapped: number;
  removed: number;
  added: number;
  notSelected: number;
  fromScript: number;
};

export type HistoryState = {
  past: OverrideSnapshot[];
  present: OverrideSnapshot;
  future: OverrideSnapshot[];
};

const emptySnapshot = (): OverrideSnapshot => ({
  addedSources: [],
  removedTargets: [],
  removedJsonMappings: [],
  overrideMappings: [],
  targetOrder: [],
});

export function emptyDraft(sapColumns: SapColumn[] = []): MappingDraft {
  return {
    json: null,
    sapColumns,
    addedSources: [],
    removedTargets: [],
    removedJsonMappings: [],
    overrideMappings: [],
    targetOrder: [],
  };
}

export function snapshotOf(draft: MappingDraft): OverrideSnapshot {
  return {
    addedSources: [...draft.addedSources],
    removedTargets: [...draft.removedTargets],
    removedJsonMappings: [...(draft.removedJsonMappings || [])],
    overrideMappings: draft.overrideMappings.map((row) => ({ ...row })),
    targetOrder: [...(draft.targetOrder || [])],
  };
}

export function applySnapshot(
  draft: MappingDraft,
  snapshot: OverrideSnapshot,
): MappingDraft {
  return {
    ...draft,
    addedSources: [...snapshot.addedSources],
    removedTargets: [...snapshot.removedTargets],
    removedJsonMappings: [...(snapshot.removedJsonMappings || [])],
    overrideMappings: snapshot.overrideMappings.map((row) => ({ ...row })),
    targetOrder: [...(snapshot.targetOrder || [])],
  };
}

export function createHistory(
  snapshot: OverrideSnapshot = emptySnapshot(),
): HistoryState {
  return { past: [], present: snapshot, future: [] };
}

export function pushHistory(
  history: HistoryState,
  next: OverrideSnapshot,
): HistoryState {
  const same = JSON.stringify(history.present) === JSON.stringify(next);
  if (same) return history;
  return {
    past: [...history.past, history.present].slice(-50),
    present: next,
    future: [],
  };
}

export function undoHistory(history: HistoryState): HistoryState {
  if (history.past.length === 0) return history;
  const previous = history.past[history.past.length - 1];
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  };
}

export function redoHistory(history: HistoryState): HistoryState {
  if (history.future.length === 0) return history;
  const [next, ...rest] = history.future;
  return {
    past: [...history.past, history.present],
    present: next,
    future: rest,
  };
}

export function normalizeObjectNameForCompare(
  value: string | null | undefined,
): string {
  if (!value) return "";
  let text = String(value).trim().toUpperCase();
  for (const prefix of ["ENTITYOF", "ENTITY_OF_"]) {
    if (text.startsWith(prefix)) {
      text = text.slice(prefix.length);
      break;
    }
  }
  return text.replace(/O/g, "0");
}

export function objectNamesMatch(
  entityName: string,
  sourceObject: string | null | undefined,
): boolean {
  if (!sourceObject || !String(sourceObject).trim()) return true;
  return (
    normalizeObjectNameForCompare(entityName) ===
    normalizeObjectNameForCompare(sourceObject)
  );
}

export function validateMappingJsonStructure(
  data: unknown,
): { path: string; message: string }[] {
  const errors: { path: string; message: string }[] = [];
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return [{ path: "$", message: "Root must be a JSON object" }];
  }
  const root = data as Record<string, unknown>;
  if (!root.source || typeof root.source !== "object") {
    errors.push({
      path: "source",
      message: "Required key 'source' is missing",
    });
  }
  if (!root.target || typeof root.target !== "object") {
    errors.push({
      path: "target",
      message: "Required key 'target' is missing",
    });
  }
  if (!Array.isArray(root.field_mapping)) {
    errors.push({
      path: "field_mapping",
      message: "Required key 'field_mapping' is missing",
    });
    return errors;
  }
  root.field_mapping.forEach((row, idx) => {
    if (!row || typeof row !== "object") {
      errors.push({
        path: `field_mapping[${idx}]`,
        message: "Must be an object",
      });
      return;
    }
    const item = row as Record<string, unknown>;
    const sources = Array.isArray(item.src_fieldnames)
      ? item.src_fieldnames.filter((name) => String(name || "").trim())
      : [];
    if (sources.length === 0 && mappingTypeRequiresSource(item)) {
      errors.push({
        path: `field_mapping[${idx}].src_fieldnames`,
        message: "src_fieldnames is missing",
      });
    }
    if (!item.tgt_fieldname || !String(item.tgt_fieldname).trim()) {
      errors.push({
        path: `field_mapping[${idx}].tgt_fieldname`,
        message: "tgt_fieldname is missing",
      });
    }
  });
  return errors;
}

const NON_SOURCE_MAPPING_TYPES = new Set(["CONSTANT", "SCRIPT", "EXPRESSION"]);

function hasConstantValue(item: Record<string, unknown>): boolean {
  const constant = item.constant_value;
  return (
    constant !== undefined &&
    constant !== null &&
    String(constant).trim() !== ""
  );
}

function mappingTypeRequiresSource(item: Record<string, unknown>): boolean {
  if (hasConstantValue(item)) return false;
  const mappingType = item.mapping_type
    ? String(item.mapping_type).trim().toUpperCase()
    : "";
  if (!mappingType || NON_SOURCE_MAPPING_TYPES.has(mappingType)) return false;
  return true;
}

function truthyKey(value: unknown): boolean {
  if (value === true || value === 1) return true;
  if (typeof value === "string") {
    const text = value.trim().toUpperCase();
    return text === "X" || text === "Y" || text === "TRUE" || text === "1";
  }
  return false;
}

function recordIsKey(field: Record<string, unknown> | null): boolean {
  if (!field) return false;
  return (
    truthyKey(field.key_flag) ||
    truthyKey(field.keyflag) ||
    truthyKey(field.is_key) ||
    truthyKey(field.key) ||
    truthyKey(field.isKey)
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function fieldList(
  raw: Record<string, unknown> | null,
  side: "source" | "target",
): FieldMeta[] {
  const node = asRecord(raw?.[side]);
  const fields = node?.fields;
  if (!Array.isArray(fields)) return [];
  return fields
    .map((field) => asRecord(field))
    .filter((field): field is Record<string, unknown> =>
      Boolean(field?.fieldname),
    )
    .map((field) => ({
      fieldname: String(field.fieldname),
      datatype: field.datatype ? String(field.datatype) : undefined,
      length: Number(field.length || 0) || undefined,
      decimals: Number(field.decimals || 0) || undefined,
      isKey: recordIsKey(field),
    }));
}

type JsonPair = {
  source: string | null;
  target: string;
  constantValue: string | null;
  isKey: boolean;
};

function jsonPairs(raw: Record<string, unknown> | null): JsonPair[] {
  const list = raw?.field_mapping;
  if (!Array.isArray(list)) return [];
  return list
    .map((row) => asRecord(row))
    .filter((row): row is Record<string, unknown> => Boolean(row))
    .map((row) => {
      const sources = Array.isArray(row.src_fieldnames)
        ? row.src_fieldnames
        : [];
      const source = sources[0] ? String(sources[0]) : null;
      const constant = hasConstantValue(row)
        ? String(row.constant_value)
        : null;
      return {
        source,
        target: String(row.tgt_fieldname || ""),
        constantValue: constant,
        isKey: recordIsKey(row),
      };
    })
    .filter((row) => row.target);
}

export function formatFieldType(
  datatype?: string,
  length?: number,
  decimals?: number,
): string {
  if (!datatype) return "";
  if (decimals) return `${datatype} ${length || 0},${decimals}`;
  return length ? `${datatype} ${length}` : datatype;
}

function metaOf(
  fields: FieldMeta[],
  name: string | null,
): FieldMeta | undefined {
  if (!name) return undefined;
  return fields.find((field) => field.fieldname === name);
}

function computeStatus(
  row: {
    source: string | null;
    target: string;
    origin: MappingOrigin;
    overridden: boolean;
    constantValue?: string | null;
  },
  sourceFields: FieldMeta[],
  targetFields: FieldMeta[],
  sapNames: Set<string>,
): string {
  if (!row.source) return row.constantValue ? "Constant" : "From script";
  if (sapNames.size > 0 && !sapNames.has(row.source)) return "Not in SAP";
  if (row.origin === "USER") return "Added by user";
  const src = metaOf(sourceFields, row.source);
  const tgt = metaOf(targetFields, row.target);
  const srcType = src?.datatype || "";
  const tgtType = tgt?.datatype || "";
  if (
    srcType === "DATS" &&
    (tgtType === "DATS" || tgtType === "DATE" || !tgtType)
  ) {
    return "Date check";
  }
  if (srcType === "CURR" && tgtType === "CURR") {
    const srcLen = src?.length || 0;
    const tgtLen = tgt?.length || 0;
    const srcDec = src?.decimals || 0;
    const tgtDec = tgt?.decimals || 0;
    if (tgtLen < srcLen || tgtDec < srcDec) return "Narrowed";
  }
  if (row.overridden) return "Overridden";
  return "Direct";
}

type WorkingRow = {
  id: string;
  source: string | null;
  target: string;
  origin: MappingOrigin;
  locked: boolean;
  overridden: boolean;
  constantValue: string | null;
  isKey: boolean;
};

function buildWorkingRows(draft: MappingDraft): WorkingRow[] {
  const raw = draft.json?.raw ?? null;
  const sourceFields = fieldList(raw, "source");
  const targetFields = fieldList(raw, "target");
  const sapKeys = new Set(
    draft.sapColumns
      .filter((column) => column.isKey)
      .map((column) => column.name),
  );
  if (raw && Array.isArray(raw.field_mapping)) {
    const mappedTargets = new Set<string>();
    const base = jsonPairs(raw).map((row) => {
      mappedTargets.add(row.target);
      const sourceMeta = metaOf(sourceFields, row.source);
      const targetMeta = metaOf(targetFields, row.target);
      return {
        id: `json:${row.target}`,
        source: row.source,
        target: row.target,
        origin: "JSON" as const,
        locked: true,
        overridden: false,
        constantValue: row.constantValue,
        isKey: Boolean(
          row.isKey ||
            sourceMeta?.isKey ||
            targetMeta?.isKey ||
            (row.source && sapKeys.has(row.source)),
        ),
      };
    });
    for (const field of targetFields) {
      if (mappedTargets.has(field.fieldname)) continue;
      base.push({
        id: `script:${field.fieldname}`,
        source: null,
        target: field.fieldname,
        origin: "JSON",
        locked: true,
        overridden: false,
        constantValue: null,
        isKey: Boolean(field.isKey),
      });
    }
    return base;
  }
  return draft.sapColumns.map((column) => ({
    id: `id:${column.name}`,
    source: column.name,
    target: column.name,
    origin: "IDENTITY" as const,
    locked: false,
    overridden: false,
    constantValue: null,
    isKey: Boolean(column.isKey),
  }));
}

function toMappingRow(
  row: WorkingRow,
  sourceFields: FieldMeta[],
  targetFields: FieldMeta[],
  sapNames: Set<string>,
): MappingRow {
  return {
    id: row.id,
    source: row.source,
    target: row.target,
    origin: row.origin,
    targetLocked: row.locked,
    overridden: row.overridden,
    status: computeStatus(row, sourceFields, targetFields, sapNames),
    isKey: row.isKey,
  };
}

export function selectRemovedMappings(draft: MappingDraft): MappingRow[] {
  const raw = draft.json?.raw ?? null;
  const removedIds = new Set(draft.removedJsonMappings || []);
  const sourceFields = fieldList(raw, "source");
  const targetFields = fieldList(raw, "target");
  const sapNames = new Set(draft.sapColumns.map((column) => column.name));
  return buildWorkingRows(draft)
    .filter((row) => removedIds.has(row.id))
    .map((row) => toMappingRow(row, sourceFields, targetFields, sapNames));
}

export function selectEffectiveMappings(draft: MappingDraft): MappingRow[] {
  const raw = draft.json?.raw ?? null;
  const removed = new Set(
    draft.removedTargets.map((name) => name.toUpperCase()),
  );
  const removedIds = new Set(draft.removedJsonMappings || []);
  const sourceFields = fieldList(raw, "source");
  const targetFields = fieldList(raw, "target");
  const sapNames = new Set(draft.sapColumns.map((column) => column.name));
  const sapKeys = new Set(
    draft.sapColumns
      .filter((column) => column.isKey)
      .map((column) => column.name),
  );

  const base = buildWorkingRows(draft).filter(
    (row) => !removedIds.has(row.id) && !removed.has(row.target.toUpperCase()),
  );
  const byId = new Map(base.map((row) => [row.id, { ...row }]));
  const byTarget = new Map(
    [...byId.values()].map((row) => [row.target.toUpperCase(), row.id]),
  );

  for (const override of draft.overrideMappings) {
    if (override.kind === "repoint") {
      const key = (override.jsonTarget || override.target).toUpperCase();
      const row = byId.get(byTarget.get(key) || "");
      if (row) {
        row.source = override.source || null;
        row.overridden = row.origin === "JSON";
      }
    } else if (override.kind === "rename") {
      const row =
        (override.id && byId.get(override.id)) ||
        byId.get(byTarget.get((override.jsonTarget || "").toUpperCase()) || "");
      if (row && override.target && !row.locked) {
        byTarget.delete(row.target.toUpperCase());
        row.target = override.target;
        if (override.source) row.source = override.source;
        byTarget.set(row.target.toUpperCase(), row.id);
      }
    } else if (
      override.kind === "added" &&
      override.source &&
      override.target
    ) {
      const targetKey = override.target.toUpperCase();
      const existingId = byTarget.get(targetKey);
      if (existingId) {
        const existing = byId.get(existingId);
        if (existing && !existing.source) {
          existing.source = override.source;
          existing.origin = "USER";
        }
        continue;
      }
      const id = override.id || `user:${override.target}`;
      byId.set(id, {
        id,
        source: override.source,
        target: override.target,
        origin: "USER",
        locked: false,
        overridden: false,
        constantValue: null,
        isKey: Boolean(override.source && sapKeys.has(override.source)),
      });
      byTarget.set(targetKey, id);
    }
  }

  const usedSources = new Set(
    [...byId.values()]
      .map((row) => row.source)
      .filter((name): name is string => Boolean(name)),
  );
  for (const source of draft.addedSources) {
    if (usedSources.has(source)) continue;
    const targetKey = source.toUpperCase();
    const existingId = byTarget.get(targetKey);
    if (existingId) {
      const existing = byId.get(existingId);
      if (existing && !existing.source) {
        existing.source = source;
        existing.origin = "USER";
        usedSources.add(source);
      }
      continue;
    }
    const id = `user:${source}`;
    byId.set(id, {
      id,
      source,
      target: source,
      origin: "USER",
      locked: false,
      overridden: false,
      constantValue: null,
      isKey: sapKeys.has(source),
    });
    byTarget.set(targetKey, id);
    usedSources.add(source);
  }

  const mapped = [...byId.values()].map((row) =>
    toMappingRow(row, sourceFields, targetFields, sapNames),
  );
  const order = draft.targetOrder || [];
  if (order.length === 0) return mapped;
  const rank = new Map(order.map((name, index) => [name.toUpperCase(), index]));
  return mapped
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftRank = rank.has(left.row.target.toUpperCase())
        ? (rank.get(left.row.target.toUpperCase()) as number)
        : Number.MAX_SAFE_INTEGER;
      const rightRank = rank.has(right.row.target.toUpperCase())
        ? (rank.get(right.row.target.toUpperCase()) as number)
        : Number.MAX_SAFE_INTEGER;
      if (leftRank !== rightRank) return leftRank - rightRank;
      return left.index - right.index;
    })
    .map((item) => item.row);
}

export function mappingCounters(
  rows: MappingRow[],
  sapColumns: SapColumn[],
  removedRows: MappingRow[] = [],
): MappingCounters {
  const removedJson = removedRows.filter((row) => row.id.startsWith("json:"));
  const jsonMapped = rows.filter(
    (row) => row.origin === "JSON" && Boolean(row.source),
  ).length;
  const identityMapped = rows.filter((row) => row.origin === "IDENTITY").length;
  const occupied = new Set<string>();
  rows.forEach((row) => {
    if (row.source) occupied.add(row.source);
  });
  removedJson.forEach((row) => {
    if (row.source) occupied.add(row.source);
  });
  return {
    mapped:
      jsonMapped > 0 || removedJson.length > 0 ? jsonMapped : identityMapped,
    removed: removedJson.length,
    added: rows.filter((row) => row.origin === "USER").length,
    notSelected: sapColumns.filter((column) => !occupied.has(column.name))
      .length,
    fromScript: rows.filter((row) => row.origin === "JSON" && !row.source)
      .length,
  };
}

export function columnsRemainMapped(
  _rows: MappingRow[],
  counters: MappingCounters,
  hasJson: boolean,
): boolean {
  if (!hasJson) {
    // No DTP JSON: allow script-only / cleared column mapping; identity rows are optional.
    return true;
  }
  return counters.mapped + counters.added > 0;
}

export function keyRemovalWarning(
  row: MappingRow | undefined,
  sapColumns: SapColumn[],
): string | null {
  if (!row) return null;
  const sourceKey = Boolean(
    row.source &&
      sapColumns.some((column) => column.name === row.source && column.isKey),
  );
  if (!row.isKey && !sourceKey) return null;
  return `${row.source || row.target} is a key column. Removing it may affect loads.`;
}

export function jsonSourceNames(
  raw: Record<string, unknown> | null,
): Set<string> {
  return new Set(
    jsonPairs(raw)
      .map((row) => row.source)
      .filter((name): name is string => Boolean(name)),
  );
}

export function targetNameError(
  name: string,
  rows: MappingRow[],
  selfId: string,
): string | null {
  const trimmed = name.trim();
  if (!trimmed) return "Target name cannot be empty.";
  if (!isValidSnowflakeDestinationName(trimmed)) {
    return "Target name is not a valid Snowflake identifier.";
  }
  const upper = trimmed.toUpperCase();
  const clash = rows.find(
    (row) => row.id !== selfId && row.target.toUpperCase() === upper,
  );
  if (!clash) return null;
  if (clash.targetLocked) {
    return `Target name clashes with JSON column ${clash.target}.`;
  }
  return "Target name must be unique.";
}

export function repointMapping(
  draft: MappingDraft,
  targetId: string,
  newSource: string,
): MappingDraft {
  const rows = selectEffectiveMappings(draft);
  const row = rows.find((item) => item.id === targetId);
  if (!row || !newSource) return draft;
  const without = draft.overrideMappings.filter(
    (item) => item.id !== targetId && item.jsonTarget !== row.target,
  );
  const next: SavedMappingOverride = {
    id: targetId,
    kind: "repoint",
    source: newSource,
    target: row.target,
    jsonTarget: row.origin === "JSON" ? row.target : row.target,
  };
  return { ...draft, overrideMappings: [...without, next] };
}

export function renameTarget(
  draft: MappingDraft,
  targetId: string,
  newName: string,
): MappingDraft {
  const rows = selectEffectiveMappings(draft);
  const row = rows.find((item) => item.id === targetId);
  if (!row || row.targetLocked) return draft;
  const error = targetNameError(newName, rows, targetId);
  if (error) return draft;
  const without = draft.overrideMappings.filter((item) => item.id !== targetId);
  const nextName = newName.trim().toUpperCase();
  return {
    ...draft,
    targetOrder: draft.targetOrder?.map((name) =>
      name.toUpperCase() === row.target.toUpperCase() ? nextName : name,
    ),
    overrideMappings: [
      ...without,
      {
        id: targetId,
        kind: row.origin === "USER" ? "added" : "rename",
        source: row.source,
        target: nextName,
        jsonTarget: row.origin === "IDENTITY" ? row.source : row.target,
      },
    ],
  };
}

export function reorderMappedColumns(
  draft: MappingDraft,
  orderedIds: string[],
): MappingDraft {
  const rows = selectEffectiveMappings(draft);
  const byId = new Map(rows.map((row) => [row.id, row.target]));
  const targetOrder = orderedIds
    .map((id) => byId.get(id))
    .filter((name): name is string => Boolean(name));
  return { ...draft, targetOrder };
}

export function clearUserMapping(
  draft: MappingDraft,
  targetId: string,
): MappingDraft {
  const rows = selectEffectiveMappings(draft);
  const row = rows.find((item) => item.id === targetId);
  if (!row) return draft;
  if (row.origin === "JSON") {
    if ((draft.removedJsonMappings || []).includes(row.id)) return draft;
    return {
      ...draft,
      removedJsonMappings: [...(draft.removedJsonMappings || []), row.id],
      overrideMappings: draft.overrideMappings.filter(
        (item) => item.id !== row.id && item.jsonTarget !== row.target,
      ),
    };
  }
  if (row.origin === "USER") {
    return {
      ...draft,
      addedSources: draft.addedSources.filter((name) => name !== row.source),
      overrideMappings: draft.overrideMappings.filter(
        (item) => item.id !== targetId,
      ),
    };
  }
  if (row.origin === "IDENTITY") {
    return {
      ...draft,
      removedTargets: [...draft.removedTargets, row.target],
    };
  }
  return draft;
}

export function restoreJsonMapping(
  draft: MappingDraft,
  targetId: string,
): MappingDraft {
  return {
    ...draft,
    removedJsonMappings: (draft.removedJsonMappings || []).filter(
      (id) => id !== targetId,
    ),
  };
}

export function restoreAllJsonMappings(draft: MappingDraft): MappingDraft {
  return { ...draft, removedJsonMappings: [] };
}

export function setJsonSourcesIncluded(
  draft: MappingDraft,
  sourceNames: string[],
  included: boolean,
): MappingDraft {
  const names = new Set(sourceNames);
  const matches = buildWorkingRows(draft).filter(
    (row) =>
      row.id.startsWith("json:") &&
      Boolean(row.source) &&
      names.has(row.source || ""),
  );
  const ids = new Set(matches.map((row) => row.id));
  const targets = new Set(matches.map((row) => row.target));
  if (included) {
    return {
      ...draft,
      removedJsonMappings: (draft.removedJsonMappings || []).filter(
        (id) => !ids.has(id),
      ),
    };
  }
  const next = new Set(draft.removedJsonMappings || []);
  ids.forEach((id) => next.add(id));
  return {
    ...draft,
    removedJsonMappings: [...next],
    overrideMappings: draft.overrideMappings.filter(
      (item) =>
        !ids.has(item.id) && !(item.jsonTarget && targets.has(item.jsonTarget)),
    ),
  };
}

export function addUserTarget(
  draft: MappingDraft,
  sourceName: string,
): MappingDraft {
  const rows = selectEffectiveMappings(draft);
  let target = sourceName.toUpperCase();
  let suffix = 2;
  while (rows.some((row) => row.target.toUpperCase() === target)) {
    target = `${sourceName.toUpperCase()}_${suffix}`;
    suffix += 1;
  }
  return {
    ...draft,
    addedSources: draft.addedSources.includes(sourceName)
      ? draft.addedSources
      : [...draft.addedSources, sourceName],
    overrideMappings: [
      ...draft.overrideMappings,
      {
        id: `user:${sourceName}:${target}`,
        kind: "added",
        source: sourceName,
        target,
      },
    ],
  };
}

export function toggleAddedSource(
  draft: MappingDraft,
  sourceName: string,
  checked: boolean,
): MappingDraft {
  if (checked) {
    if (draft.addedSources.includes(sourceName)) return draft;
    return {
      ...draft,
      addedSources: [...draft.addedSources, sourceName],
      overrideMappings: [
        ...draft.overrideMappings,
        {
          id: `user:${sourceName}`,
          kind: "added",
          source: sourceName,
          target: sourceName,
        },
      ],
    };
  }
  return {
    ...draft,
    addedSources: draft.addedSources.filter((name) => name !== sourceName),
    overrideMappings: draft.overrideMappings.filter(
      (item) =>
        !(
          item.kind === "added" &&
          (item.id === `user:${sourceName}` || item.source === sourceName)
        ),
    ),
  };
}

export function resetToJson(draft: MappingDraft): MappingDraft {
  return {
    ...draft,
    addedSources: [],
    removedTargets: [],
    removedJsonMappings: [],
    overrideMappings: [],
    targetOrder: [],
  };
}

export function jsonFileFromRaw(
  raw: Record<string, unknown>,
  fileName: string,
  size: number,
): MappingJsonFile {
  const source = asRecord(raw.source);
  const target = asRecord(raw.target);
  const sourceObject = source?.objectname ? String(source.objectname) : null;
  const tableNameRaw = target?.objectname ? String(target.objectname) : null;
  return { fileName, size, raw, sourceObject, tableNameRaw };
}

export function keepApplicableOverrides(
  snapshot: OverrideSnapshot,
  nextRaw: Record<string, unknown>,
  sapColumns: SapColumn[],
): OverrideSnapshot {
  const targets = new Set(
    jsonPairs(nextRaw).map((row) => row.target.toUpperCase()),
  );
  const sources = new Set([
    ...jsonSourceNames(nextRaw),
    ...sapColumns.map((column) => column.name),
  ]);
  const overrideMappings = snapshot.overrideMappings.filter((item) => {
    if (item.kind === "added")
      return Boolean(item.source && sources.has(item.source));
    const jsonTarget = (item.jsonTarget || item.target).toUpperCase();
    return targets.has(jsonTarget);
  });
  const targetFields = fieldList(nextRaw, "target");
  const scriptIds = new Set(
    targetFields
      .filter((field) => !targets.has(field.fieldname.toUpperCase()))
      .map((field) => `script:${field.fieldname}`),
  );
  const jsonIds = new Set(
    jsonPairs(nextRaw).map((row) => `json:${row.target}`),
  );
  const removedJsonMappings = (snapshot.removedJsonMappings || []).filter(
    (id) => jsonIds.has(id) || scriptIds.has(id),
  );
  const addedSources = snapshot.addedSources.filter((name) =>
    sources.has(name),
  );
  const knownTargets = new Set(
    [...targets, ...addedSources.map((name) => name.toUpperCase())].map(
      (name) => name.toUpperCase(),
    ),
  );
  return {
    addedSources,
    removedTargets: [],
    removedJsonMappings,
    overrideMappings,
    targetOrder: (snapshot.targetOrder || []).filter((name) =>
      knownTargets.has(name.toUpperCase()),
    ),
  };
}

export type MappingBaseline = {
  mappingJson: Record<string, unknown> | null;
  overrides: OverrideSnapshot;
  scriptText: string;
  entryPoint: string;
  runOrder: string;
  destName: string;
  destSource: string;
};

export type MappingChangeInput = {
  mappingJson: Record<string, unknown> | null;
  overrides: OverrideSnapshot;
  scriptText: string;
  destName: string;
  sapColumns: SapColumn[];
};

const canonicalOverrides = (snapshot: OverrideSnapshot) => ({
  addedSources: [...snapshot.addedSources],
  removedTargets: [...snapshot.removedTargets],
  removedJsonMappings: [...(snapshot.removedJsonMappings || [])],
  overrideMappings: snapshot.overrideMappings.map((row) => ({
    id: row.id,
    kind: row.kind,
    source: row.source,
    target: row.target,
    jsonTarget: row.jsonTarget ?? null,
  })),
  targetOrder: [...(snapshot.targetOrder || [])],
});

const normalizeBaseline = (value: MappingBaseline) => ({
  mappingJson: value.mappingJson,
  overrides: canonicalOverrides(value.overrides),
  scriptText: value.scriptText,
  entryPoint: value.entryPoint,
  runOrder: value.runOrder,
  destName: value.destName.trim().toUpperCase(),
  destSource: value.destSource,
});

export function isMappingDirty(
  baseline: MappingBaseline,
  current: MappingBaseline,
): boolean {
  return (
    JSON.stringify(normalizeBaseline(baseline)) !==
    JSON.stringify(normalizeBaseline(current))
  );
}

const draftFromChange = (value: MappingChangeInput): MappingDraft => ({
  json: value.mappingJson
    ? jsonFileFromRaw(value.mappingJson, "mapping", 0)
    : null,
  sapColumns: value.sapColumns,
  addedSources: [...value.overrides.addedSources],
  removedTargets: [...value.overrides.removedTargets],
  removedJsonMappings: [...(value.overrides.removedJsonMappings || [])],
  overrideMappings: value.overrides.overrideMappings.map((row) => ({ ...row })),
  targetOrder: [...(value.overrides.targetOrder || [])],
});

const chipCount = (count: number, singular: string, plural: string) => {
  if (count === 1) return `1 ${singular}`;
  return `${count} ${plural}`;
};

export function summarizeMappingChanges(
  baseline: MappingChangeInput,
  current: MappingChangeInput,
): string[] {
  const before = selectEffectiveMappings(draftFromChange(baseline));
  const after = selectEffectiveMappings(draftFromChange(current));
  const beforeById = new Map(before.map((row) => [row.id, row]));
  const afterById = new Map(after.map((row) => [row.id, row]));
  let added = 0;
  let removed = 0;
  let renamed = 0;
  after.forEach((row) => {
    const previous = beforeById.get(row.id);
    if (!previous) {
      added += 1;
      return;
    }
    if (previous.target.toUpperCase() !== row.target.toUpperCase())
      renamed += 1;
  });
  before.forEach((row) => {
    if (!afterById.has(row.id)) removed += 1;
  });
  const chips: string[] = [];
  if (added > 0) chips.push(chipCount(added, "column added", "columns added"));
  if (removed > 0) chips.push(chipCount(removed, "removed", "removed"));
  if (renamed > 0) chips.push(chipCount(renamed, "renamed", "renamed"));
  if (baseline.scriptText !== current.scriptText) chips.push("script edited");
  if (
    baseline.destName.trim().toUpperCase() !==
    current.destName.trim().toUpperCase()
  ) {
    chips.push("destination table changed");
  }
  return chips;
}

export function resetToJsonImpact(draft: MappingDraft): {
  restored: number;
  discardedAdded: number;
  renames: number;
} {
  const current = selectEffectiveMappings(draft);
  const reset = selectEffectiveMappings(resetToJson(draft));
  const currentIds = new Set(current.map((row) => row.id));
  const resetById = new Map(reset.map((row) => [row.id, row]));
  return {
    restored: reset.filter((row) => !currentIds.has(row.id)).length,
    discardedAdded: current.filter((row) => row.origin === "USER").length,
    renames: current.filter((row) => {
      const base = resetById.get(row.id);
      return Boolean(
        base && base.target.toUpperCase() !== row.target.toUpperCase(),
      );
    }).length,
  };
}

export function rowFilterSummary(
  raw: Record<string, unknown> | null,
): string | null {
  const filter = asRecord(raw?.row_filter);
  if (!filter) return null;
  const field = filter.field || filter.fieldname || filter.column;
  const values = filter.values || filter.value;
  if (!field) return null;
  if (Array.isArray(values)) return `${field} in ${values.join(", ")}`;
  if (values) return `${field} = ${values}`;
  return String(field);
}
