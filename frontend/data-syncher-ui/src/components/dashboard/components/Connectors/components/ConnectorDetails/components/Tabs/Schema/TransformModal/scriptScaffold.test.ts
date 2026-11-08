import { describe, expect, it } from "vitest";

import { buildScriptExamples } from "./scriptExamples";
import {
  TRANSFORM_ENTRY_POINT,
  TRANSFORM_SHELL_HEADER,
  TRANSFORM_SHELL_HEADER_LINES,
  TRANSFORM_SHELL_RETURN,
  defaultScriptFileName,
  deriveRunOrder,
  inputSchemaFields,
  mappingFieldDetails,
  mappingFieldNames,
  normalizeTransformScript,
  outputSchemaFields,
  persistedScriptText,
  sapDatatypeToPandasDtype,
  scriptBodyLine,
  scriptErrorLine,
  unwrapTransformBody,
  validateTransformRoot,
  wrapTransformBody,
} from "./scriptScaffold";

const LEGACY_SHELL = `import pandas as pd

def transform(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()`;

describe("transform script scaffold", () => {
  it("wraps an empty body in the locked shell", () => {
    const script = wrapTransformBody("");
    expect(script.startsWith(TRANSFORM_SHELL_HEADER)).toBe(true);
    expect(script.trimEnd().endsWith(TRANSFORM_SHELL_RETURN)).toBe(true);
    expect(script).toContain(`def ${TRANSFORM_ENTRY_POINT}(__source_df:`);
    expect(script).toContain("__target_df = __source_df.copy()");
    expect(script).toContain("df = __target_df");
    expect(unwrapTransformBody(script)).toBe("");
    expect(TRANSFORM_SHELL_HEADER_LINES).toBe(5);
  });

  it("round-trips the editable body", () => {
    const body =
      'df["BUKRS"] = df["BUKRS"].str.strip()\nif True:\n    df["X"] = 1';
    const script = wrapTransformBody(body);
    expect(unwrapTransformBody(script)).toBe(body);
    expect(normalizeTransformScript(script)).toBe(script);
  });

  it("keeps an old df shell and captures a reassigned df", () => {
    const uploaded = `${LEGACY_SHELL}\n    df = df[df["A"] == 1]\n    return df\n`;
    expect(unwrapTransformBody(uploaded)).toBe(
      'df = df[df["A"] == 1]\n__target_df = df',
    );
    const normalized = normalizeTransformScript(uploaded);
    expect(normalized.startsWith(TRANSFORM_SHELL_HEADER)).toBe(true);
    expect(unwrapTransformBody(normalized)).toBe(
      'df = df[df["A"] == 1]\n__target_df = df',
    );
  });

  it("leaves in-place df edits unchanged when loading an old shell", () => {
    const uploaded = `${LEGACY_SHELL}\n    df["X"] = 1\n    return df\n`;
    expect(unwrapTransformBody(uploaded)).toBe('df["X"] = 1');
  });

  it("pulls a single uploaded function and its assignments into the body", () => {
    const uploaded = [
      "import pandas as pd",
      "",
      "AMOUNT = 10",
      "",
      "def old_transform(source_df):",
      "    out = source_df.copy()",
      "    return out",
      "",
    ].join("\n");
    expect(unwrapTransformBody(uploaded)).toBe(
      "AMOUNT = 10\nout = source_df.copy()\nreturn out",
    );
    const wrapped = normalizeTransformScript(uploaded);
    expect(wrapped.startsWith(TRANSFORM_SHELL_HEADER)).toBe(true);
    expect(unwrapTransformBody(wrapped)).toBe(
      "AMOUNT = 10\nout = source_df.copy()\nreturn out",
    );
  });

  it("inserts a file with more than one function as the body", () => {
    const uploaded = "def a(df):\n    return df\n\ndef b(df):\n    return df\n";
    expect(unwrapTransformBody(uploaded)).toBe(uploaded.trim());
  });

  it("reads source and target field names and types from mapping JSON", () => {
    const mapping = {
      source: {
        fields: [
          { fieldname: "BUKRS", datatype: "CHAR", length: 4 },
          { fieldname: "DMBTR", datatype: "CURR", length: 23, decimals: 2 },
          { fieldname: "NOTE" },
        ],
      },
      target: {
        fields: [
          { fieldname: "BUKRS", datatype: "CHAR", length: 4 },
          { fieldname: "" },
        ],
      },
    };
    expect(mappingFieldNames(mapping, "source")).toEqual([
      "BUKRS",
      "DMBTR",
      "NOTE",
    ]);
    expect(mappingFieldDetails(mapping, "source")).toEqual([
      { name: "BUKRS", typeLabel: "CHAR 4" },
      { name: "DMBTR", typeLabel: "CURR 23,2" },
      { name: "NOTE", typeLabel: "" },
    ]);
    expect(mappingFieldNames(mapping, "target")).toEqual(["BUKRS"]);
    expect(mappingFieldNames(null, "source")).toEqual([]);
    expect(mappingFieldDetails(null, "target")).toEqual([]);
  });

  it("includes mapped sources added on the mapping step", () => {
    const mapping = {
      source: {
        fields: [{ fieldname: "BUKRS", datatype: "CHAR", length: 4 }],
      },
    };
    expect(
      inputSchemaFields(
        [
          { source: "BUKRS", target: "BUKRS" },
          { source: "STBLG", target: "STBLG" },
        ],
        mapping,
        [{ name: "STBLG", type: "CHAR", length: 10 }],
      ),
    ).toEqual([
      { name: "BUKRS", dtype: "string" },
      { name: "STBLG", dtype: "string" },
    ]);
  });

  it("includes JSON targets and columns added on the mapping step", () => {
    const mapping = {
      target: {
        fields: [
          { fieldname: "BUKRS", datatype: "CHAR", length: 4 },
          { fieldname: "UPD_DATE", datatype: "DATS", length: 8 },
        ],
      },
    };
    expect(
      outputSchemaFields(
        [
          { source: "BUKRS", target: "BUKRS" },
          { source: null, target: "UPD_DATE" },
          { source: "STBLG", target: "STBLG" },
        ],
        mapping,
        [{ name: "STBLG", type: "CHAR", length: 10 }],
      ),
    ).toEqual([
      { name: "BUKRS", dtype: "string" },
      { name: "UPD_DATE", dtype: "string" },
      { name: "STBLG", dtype: "string" },
    ]);
  });

  it("maps SAP datatypes to pandas dtypes for script schema folds", () => {
    expect(sapDatatypeToPandasDtype("CHAR", 0)).toBe("string");
    expect(sapDatatypeToPandasDtype("CHAR 4", 0)).toBe("string");
    expect(sapDatatypeToPandasDtype("DATS", 0)).toBe("string");
    expect(sapDatatypeToPandasDtype("CURR", 2)).toBe("float64");
    expect(sapDatatypeToPandasDtype("CURR 23,2", 0)).toBe("float64");
    expect(sapDatatypeToPandasDtype("INT4", 0)).toBe("Int64");
    expect(sapDatatypeToPandasDtype("DATE", 0)).toBe("datetime64[ns]");
    expect(sapDatatypeToPandasDtype(undefined)).toBe("object");
  });

  it("saves an empty script when transformation is off or the shell has no body", () => {
    const shell = wrapTransformBody("");
    expect(persistedScriptText(false, shell)).toBe("");
    expect(persistedScriptText(true, shell)).toBe("");
    expect(
      persistedScriptText(true, wrapTransformBody('df["A"] = 1')),
    ).toContain('df["A"] = 1');
  });

  it("reads the script line from a sandbox traceback", () => {
    const traceback = [
      "Traceback (most recent call last):",
      '  File "<transform_script>", line 8, in transform',
      '    __target_df["AMT"] = __source_df["MISSING"]',
      "KeyError: 'MISSING'",
    ].join("\n");
    expect(scriptErrorLine(traceback, 2)).toBe(8);
    expect(scriptErrorLine(undefined, 4)).toBe(4);
    expect(scriptErrorLine("", 0)).toBeUndefined();
  });

  it("maps a wrapped-script line number onto the editable body", () => {
    expect(scriptBodyLine(undefined)).toBeUndefined();
    expect(scriptBodyLine(3)).toBe(3);
    expect(scriptBodyLine(TRANSFORM_SHELL_HEADER_LINES + 1)).toBe(1);
  });

  it("derives run order from whether a mapping JSON is loaded", () => {
    expect(deriveRunOrder(null)).toBe("filter_script_mapping");
    expect(deriveRunOrder({ field_mapping: [] })).toBe("mapping_script");
  });

  it("names the script after the JSON target object, otherwise the entity", () => {
    expect(
      defaultScriptFileName(
        { target: { objectname: "/IMO/CMFIGL04" } },
        "0FI_GL_4",
      ),
    ).toBe("IMO_CMFIGL04.py");
    expect(defaultScriptFileName(null, "0FI_GL_4")).toBe("0FI_GL_4.py");
  });

  it("requires a single top-level transform function", () => {
    expect(validateTransformRoot(wrapTransformBody('df["A"] = 1'))).toBeNull();
    expect(validateTransformRoot("def foo(df):\n    return df\n")).toEqual({
      message: "Root function must be named transform (found foo).",
      line: 1,
    });
    expect(
      validateTransformRoot(
        "def transform(df):\n    return df\n\ndef extra(df):\n    return df\n",
      ),
    ).toEqual({
      message: "Only transform may be a top-level function (found extra).",
      line: 4,
    });
    expect(validateTransformRoot("df['A'] = 1\n")).toEqual({
      message: "Root function must be named transform.",
      line: undefined,
    });
  });
});

describe("script examples", () => {
  it("uses mapping field names in the filter and transform samples", () => {
    const [filter, transform] = buildScriptExamples(
      ["BELNR", "DMBTR"],
      ["BELNR"],
    );
    expect(filter.code).toContain('__source_df["BELNR"]');
    expect(filter.code).toContain('== "IN01"');
    expect(filter.code).not.toContain(".isin(");
    expect(transform.code).toContain('__target_df["BELNR"]');
    expect(transform.code).toContain('__source_df["DMBTR"]');
    expect(transform.code).toContain("pd.to_numeric");
  });

  it("falls back to BUKRS and DMBTR when the mapping has no fields", () => {
    const [filter, transform] = buildScriptExamples([], []);
    expect(filter.code).toContain('__source_df["BUKRS"]');
    expect(filter.code).toContain('== "IN01"');
    expect(transform.code).toContain('__target_df["DMBTR"]');
  });
});
