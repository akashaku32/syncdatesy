import { describe, expect, it } from "vitest";

import {
  type MappingDraft,
  addUserTarget,
  applySnapshot,
  clearUserMapping,
  columnsRemainMapped,
  createHistory,
  emptyDraft,
  isMappingDirty,
  jsonFileFromRaw,
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
  restoreJsonMapping,
  selectEffectiveMappings,
  selectRemovedMappings,
  setJsonSourcesIncluded,
  snapshotOf,
  summarizeMappingChanges,
  targetNameError,
  toggleAddedSource,
  undoHistory,
  validateMappingJsonStructure,
} from "./mappingState";

const sap = (names: string[]) => names.map((name) => ({ name, type: "CHAR" }));

const sampleJson = {
  source: {
    objectname: "0FI_GL_4",
    fields: [
      { fieldname: "BUKRS", datatype: "CHAR", length: 4 },
      { fieldname: "DMBTR", datatype: "CURR", length: 23, decimals: 2 },
      { fieldname: "BLDAT", datatype: "DATS", length: 8 },
    ],
  },
  target: {
    objectname: "/IMO/CMFIGL04",
    fields: [
      { fieldname: "BUKRS", datatype: "CHAR", length: 4 },
      { fieldname: "DMBTR", datatype: "CURR", length: 13, decimals: 2 },
      { fieldname: "BLDAT", datatype: "DATS", length: 8 },
      { fieldname: "UPD_DATE", datatype: "DATS", length: 8 },
    ],
  },
  field_mapping: [
    { src_fieldnames: ["BUKRS"], tgt_fieldname: "BUKRS" },
    { src_fieldnames: ["DMBTR"], tgt_fieldname: "DMBTR" },
    { src_fieldnames: ["BLDAT"], tgt_fieldname: "BLDAT" },
  ],
  row_filter: { field: "BUKRS", values: ["IN01", "1710"] },
};

function draftWith(
  json: Record<string, unknown> | null,
  columns: string[],
): MappingDraft {
  const base = emptyDraft(sap(columns));
  if (!json) return base;
  return { ...base, json: jsonFileFromRaw(json, "map.json", 120) };
}

describe("objectNamesMatch", () => {
  it("treats letter O and digit 0 as the same", () => {
    expect(objectNamesMatch("EntityOfOFI_GL_4", "0FI_GL_4")).toBe(true);
    expect(objectNamesMatch("EntityOf0FI_GL_4", "0FI_GL_4")).toBe(true);
    expect(objectNamesMatch("EntityOf0FI_GL_4", "0FI_AR_30")).toBe(false);
  });
});

describe("selectEffectiveMappings", () => {
  it("locks JSON targets and marks from-script and narrowed rows", () => {
    const rows = selectEffectiveMappings(
      draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT", "EXTRA"]),
    );
    const bukrs = rows.find((row) => row.target === "BUKRS");
    const amount = rows.find((row) => row.target === "DMBTR");
    const script = rows.find((row) => row.target === "UPD_DATE");
    expect(bukrs?.targetLocked).toBe(true);
    expect(bukrs?.status).toBe("Direct");
    expect(amount?.status).toBe("Narrowed");
    expect(script?.status).toBe("From script");
    expect(script?.source).toBeNull();
    expect(
      mappingCounters(rows, sap(["BUKRS", "DMBTR", "BLDAT", "EXTRA"])),
    ).toEqual({
      mapped: 3,
      removed: 0,
      added: 0,
      notSelected: 1,
      fromScript: 1,
    });
  });

  it("uses identity mappings when there is no JSON", () => {
    const rows = selectEffectiveMappings(draftWith(null, ["BUKRS", "WAERS"]));
    expect(rows).toHaveLength(2);
    expect(
      rows.every(
        (row) => row.origin === "IDENTITY" && row.source === row.target,
      ),
    ).toBe(true);
  });

  it("removes a JSON mapping when its target row is cleared", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]);
    draft = clearUserMapping(draft, "json:BUKRS");
    const rows = selectEffectiveMappings(draft);
    expect(rows.find((row) => row.target === "BUKRS")).toBeUndefined();
    expect(selectRemovedMappings(draft).map((row) => row.id)).toEqual([
      "json:BUKRS",
    ]);
    expect(draft.json?.raw).toEqual(sampleJson);
  });

  it("adds a new target and suffixes the name when it already exists", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "EXTRA"]);
    draft = addUserTarget(draft, "EXTRA");
    draft = addUserTarget(draft, "BUKRS");
    const rows = selectEffectiveMappings(draft);
    expect(rows.find((row) => row.target === "EXTRA")?.origin).toBe("USER");
    expect(rows.find((row) => row.target === "BUKRS_2")?.source).toBe("BUKRS");
  });
  it("fills a script-only JSON target when adding a matching SAP column", () => {
    const json = {
      ...sampleJson,
      target: {
        ...sampleJson.target,
        fields: [
          ...sampleJson.target.fields,
          { fieldname: "EXTRA", datatype: "CHAR", length: 10 },
        ],
      },
    };
    let draft = draftWith(json, ["BUKRS", "DMBTR", "BLDAT", "EXTRA"]);
    draft = toggleAddedSource(draft, "EXTRA", true);
    const rows = selectEffectiveMappings(draft);
    const extra = rows.find((row) => row.target === "EXTRA");
    expect(extra?.source).toBe("EXTRA");
    expect(extra?.status).toBe("Added by user");
    expect(mappingCounters(rows, draft.sapColumns).added).toBe(1);
  });

  it("adds and removes a checkbox source", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT", "EXTRA"]);
    draft = toggleAddedSource(draft, "EXTRA", true);
    let rows = selectEffectiveMappings(draft);
    expect(rows.find((row) => row.target === "EXTRA")?.status).toBe(
      "Added by user",
    );
    expect(mappingCounters(rows, draft.sapColumns).added).toBe(1);
    draft = toggleAddedSource(draft, "EXTRA", false);
    rows = selectEffectiveMappings(draft);
    expect(rows.find((row) => row.target === "EXTRA")).toBeUndefined();
  });

  it("lets one source feed several targets", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]);
    draft = repointMapping(draft, "json:DMBTR", "BUKRS");
    const rows = selectEffectiveMappings(draft);
    const fed = rows.filter((row) => row.source === "BUKRS");
    expect(fed.map((row) => row.target).sort()).toEqual(["BUKRS", "DMBTR"]);
    expect(rows.find((row) => row.target === "DMBTR")?.status).toBe(
      "Overridden",
    );
  });

  it("renames a user target and rejects a JSON name clash", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "EXTRA"]);
    draft = toggleAddedSource(draft, "EXTRA", true);
    const rows = selectEffectiveMappings(draft);
    const added = rows.find((row) => row.target === "EXTRA");
    expect(added).toBeTruthy();
    expect(targetNameError("BUKRS", rows, added!.id)).toMatch(/JSON/);
    expect(targetNameError("", rows, added!.id)).toMatch(/empty/);
    draft = renameTarget(draft, added!.id, "EXTRA_COL");
    expect(
      selectEffectiveMappings(draft).some((row) => row.target === "EXTRA_COL"),
    ).toBe(true);
  });

  it("resets overrides without mutating the JSON", () => {
    const original = structuredClone(sampleJson);
    let draft = draftWith(sampleJson, ["BUKRS", "EXTRA"]);
    draft = toggleAddedSource(draft, "EXTRA", true);
    draft = resetToJson(draft);
    expect(draft.overrideMappings).toEqual([]);
    expect(draft.json?.raw).toEqual(original);
    expect(
      selectEffectiveMappings(draft).some((row) => row.origin === "USER"),
    ).toBe(false);
  });

  it("undo and redo restore override snapshots", () => {
    const start = emptyDraft(sap(["A"]));
    let history = createHistory(snapshotOf(start));
    const next = snapshotOf(toggleAddedSource(start, "A", true));
    history = pushHistory(history, next);
    history = undoHistory(history);
    expect(history.present.addedSources).toEqual([]);
    history = redoHistory(history);
    expect(history.present.addedSources).toEqual(["A"]);
  });

  it("reorders mapped columns without changing their sources", () => {
    const draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]);
    const before = selectEffectiveMappings(draft).map((row) => row.target);
    const reordered = reorderMappedColumns(draft, [
      "json:BLDAT",
      "json:BUKRS",
      "json:DMBTR",
      "script:UPD_DATE",
    ]);
    const after = selectEffectiveMappings(reordered);
    expect(after.map((row) => row.target)).toEqual([
      "BLDAT",
      "BUKRS",
      "DMBTR",
      "UPD_DATE",
    ]);
    expect(after.map((row) => row.source)).toEqual([
      "BLDAT",
      "BUKRS",
      "DMBTR",
      null,
    ]);
    expect(before).toEqual(["BUKRS", "DMBTR", "BLDAT", "UPD_DATE"]);
  });

  it("keeps only overrides that still apply to a new JSON", () => {
    const kept = keepApplicableOverrides(
      {
        addedSources: ["EXTRA"],
        removedTargets: ["GONE"],
        removedJsonMappings: [],
        overrideMappings: [
          {
            id: "json:BUKRS",
            kind: "repoint",
            source: "WAERS",
            target: "BUKRS",
            jsonTarget: "BUKRS",
          },
          {
            id: "json:MISSING",
            kind: "repoint",
            source: "BUKRS",
            target: "MISSING",
            jsonTarget: "MISSING",
          },
        ],
      },
      sampleJson,
      sap(["EXTRA", "BUKRS"]),
    );
    expect(kept.overrideMappings.map((item) => item.jsonTarget)).toEqual([
      "BUKRS",
    ]);
    expect(kept.addedSources).toEqual(["EXTRA"]);
  });

  it("reports a missing target field with its JSON path", () => {
    const errors = validateMappingJsonStructure({
      source: {},
      target: {},
      field_mapping: [{ src_fieldnames: ["A"] }],
    });
    expect(
      errors.some((error) => error.path === "field_mapping[0].tgt_fieldname"),
    ).toBe(true);
  });

  it("treats a snapshot that matches the baseline as clean, including after undo", () => {
    const baseline = {
      mappingJson: null,
      overrides: snapshotOf(emptyDraft()),
      scriptText: "",
      entryPoint: "",
      runOrder: "filter_script_mapping",
      destName: "TABLE",
      destSource: "DEFAULT",
    };
    expect(isMappingDirty(baseline, { ...baseline, destName: "table" })).toBe(
      false,
    );

    const start = emptyDraft(sap(["A"]));
    let history = createHistory(snapshotOf(start));
    history = pushHistory(
      history,
      snapshotOf(toggleAddedSource(start, "A", true)),
    );
    expect(
      isMappingDirty(baseline, { ...baseline, overrides: history.present }),
    ).toBe(true);
    history = undoHistory(history);
    expect(
      isMappingDirty(baseline, { ...baseline, overrides: history.present }),
    ).toBe(false);
  });

  it("summarises mapping changes and omits zero counts", () => {
    const columns = sap(["BUKRS"]);
    const baseline = {
      mappingJson: null,
      overrides: snapshotOf(emptyDraft(columns)),
      scriptText: "",
      destName: "GL",
      sapColumns: columns,
    };
    expect(summarizeMappingChanges(baseline, baseline)).toEqual([]);

    const added = addUserTarget(
      addUserTarget(emptyDraft(columns), "EXTRA"),
      "WAERS",
    );
    expect(
      summarizeMappingChanges(baseline, {
        ...baseline,
        overrides: snapshotOf(added),
      }),
    ).toEqual(["2 columns added"]);

    const removed = clearUserMapping(emptyDraft(columns), "id:BUKRS");
    expect(
      summarizeMappingChanges(baseline, {
        ...baseline,
        overrides: snapshotOf(removed),
      }),
    ).toEqual(["1 removed"]);

    const renamed = renameTarget(emptyDraft(columns), "id:BUKRS", "COMPANY");
    expect(
      summarizeMappingChanges(baseline, {
        ...baseline,
        overrides: snapshotOf(renamed),
        scriptText: "return df",
        destName: "GL_NEW",
      }),
    ).toEqual(["1 renamed", "script edited", "destination table changed"]);
  });
});

describe("removed JSON mappings", () => {
  it("counts removed JSON columns and leaves them out of not selected", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT", "EXTRA"]);
    draft = setJsonSourcesIncluded(draft, ["BUKRS", "DMBTR"], false);
    const rows = selectEffectiveMappings(draft);
    const removed = selectRemovedMappings(draft);
    expect(mappingCounters(rows, draft.sapColumns, removed)).toEqual({
      mapped: 1,
      removed: 2,
      added: 0,
      notSelected: 1,
      fromScript: 1,
    });
  });

  it("restores the original JSON mapping after untick", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]);
    draft = setJsonSourcesIncluded(draft, ["VALUT"], false);
    draft = setJsonSourcesIncluded(draft, ["BUKRS"], false);
    draft = restoreJsonMapping(draft, "json:BUKRS");
    const rows = selectEffectiveMappings(draft);
    const restored = rows.find((row) => row.id === "json:BUKRS");
    expect(restored?.source).toBe("BUKRS");
    expect(restored?.target).toBe("BUKRS");
    expect(restored?.targetLocked).toBe(true);
    expect(restored?.status).toBe("Direct");
  });

  it("treats a target remove as the same change as unticking the source", () => {
    const columns = ["BUKRS", "DMBTR", "BLDAT"];
    const fromTarget = clearUserMapping(
      draftWith(sampleJson, columns),
      "json:BUKRS",
    );
    const fromTray = setJsonSourcesIncluded(
      draftWith(sampleJson, columns),
      ["BUKRS"],
      false,
    );
    expect(fromTarget.removedJsonMappings).toEqual(
      fromTray.removedJsonMappings,
    );
    expect(selectEffectiveMappings(fromTarget).map((row) => row.id)).toEqual(
      selectEffectiveMappings(fromTray).map((row) => row.id),
    );
  });

  it("deletes an added column instead of parking it in the removed list", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "EXTRA"]);
    draft = addUserTarget(draft, "EXTRA");
    const added = selectEffectiveMappings(draft).find(
      (row) => row.origin === "USER",
    );
    draft = clearUserMapping(draft, added?.id || "");
    expect(
      selectEffectiveMappings(draft).some((row) => row.origin === "USER"),
    ).toBe(false);
    expect(draft.removedJsonMappings).toEqual([]);
  });

  it("treats a JSON key_flag as a key column", () => {
    const json = {
      source: { fields: [{ fieldname: "BUKRS", key_flag: "X" }] },
      target: { fields: [{ fieldname: "BUKRS", key_flag: "X" }] },
      field_mapping: [{ src_fieldnames: ["BUKRS"], tgt_fieldname: "BUKRS" }],
    };
    const draft = draftWith(json, ["BUKRS"]);
    const row = selectEffectiveMappings(draft).find(
      (item) => item.source === "BUKRS",
    );
    expect(keyRemovalWarning(row, draft.sapColumns)).toBe(
      "BUKRS is a key column. Removing it may affect loads.",
    );
  });

  it("warns for a key column and keeps the removal on the history stack", () => {
    const columns = [
      { name: "BUKRS", type: "CHAR", isKey: true },
      { name: "DMBTR", type: "CURR" },
    ];
    const start = {
      ...draftWith(sampleJson, []),
      sapColumns: columns,
    };
    const row = selectEffectiveMappings(start).find(
      (item) => item.source === "BUKRS",
    );
    expect(keyRemovalWarning(row, columns)).toBe(
      "BUKRS is a key column. Removing it may affect loads.",
    );
    let history = createHistory(snapshotOf(start));
    history = pushHistory(
      history,
      snapshotOf(clearUserMapping(start, "json:BUKRS")),
    );
    expect(
      selectEffectiveMappings(applySnapshot(start, history.present)).some(
        (item) => item.id === "json:BUKRS",
      ),
    ).toBe(false);
    history = undoHistory(history);
    expect(
      selectEffectiveMappings(applySnapshot(start, history.present)).some(
        (item) => item.id === "json:BUKRS",
      ),
    ).toBe(true);
  });

  it("restores removed fields on reset to JSON", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "EXTRA"]);
    draft = setJsonSourcesIncluded(draft, ["BUKRS"], false);
    draft = addUserTarget(draft, "EXTRA");
    draft = resetToJson(draft);
    expect(draft.removedJsonMappings).toEqual([]);
    expect(draft.addedSources).toEqual([]);
    expect(
      selectEffectiveMappings(draft).some((row) => row.id === "json:BUKRS"),
    ).toBe(true);
  });

  it("blocks when no JSON or added columns remain", () => {
    let draft = draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]);
    draft = setJsonSourcesIncluded(draft, ["BUKRS", "DMBTR", "BLDAT"], false);
    const rows = selectEffectiveMappings(draft);
    const counters = mappingCounters(
      rows,
      draft.sapColumns,
      selectRemovedMappings(draft),
    );
    expect(columnsRemainMapped(rows, counters, true)).toBe(false);
    expect(counters.mapped).toBe(0);
  });

  it("allows next without mapped columns when no mapping JSON (script-only)", () => {
    const draft: MappingDraft = {
      ...emptyDraft(sap(["BUKRS", "DMBTR"])),
      removedTargets: ["BUKRS", "DMBTR"],
    };
    const rows = selectEffectiveMappings(draft);
    const counters = mappingCounters(rows, draft.sapColumns);
    expect(rows).toHaveLength(0);
    expect(columnsRemainMapped(rows, counters, false)).toBe(true);
  });

  it("keeps selectors from returning removed mappings", () => {
    const draft = clearUserMapping(
      draftWith(sampleJson, ["BUKRS", "DMBTR", "BLDAT"]),
      "json:DMBTR",
    );
    expect(
      selectEffectiveMappings(draft).some((row) => row.target === "DMBTR"),
    ).toBe(false);
    expect(selectRemovedMappings(draft)[0]?.source).toBe("DMBTR");
  });

  it("accepts empty src_fieldnames for script and constant targets", () => {
    const errors = validateMappingJsonStructure({
      source: {},
      target: {},
      field_mapping: [
        { tgt_fieldname: "UPD_DATE" },
        {
          tgt_fieldname: "GN_R3_SSY",
          src_fieldnames: [],
          constant_value: "X",
        },
        { tgt_fieldname: "DIRECT_COL", mapping_type: "DIRECT" },
      ],
    });
    expect(errors.map((error) => error.path)).toEqual([
      "field_mapping[2].src_fieldnames",
    ]);
    const json = {
      source: { fields: [] },
      target: { fields: [] },
      field_mapping: [
        { tgt_fieldname: "UPD_DATE" },
        { tgt_fieldname: "GN_R3_SSY", constant_value: "X" },
      ],
    };
    const rows = selectEffectiveMappings(draftWith(json, ["BUKRS"]));
    expect(rows.find((row) => row.target === "UPD_DATE")?.status).toBe(
      "From script",
    );
    expect(rows.find((row) => row.target === "GN_R3_SSY")?.status).toBe(
      "Constant",
    );
  });
});
