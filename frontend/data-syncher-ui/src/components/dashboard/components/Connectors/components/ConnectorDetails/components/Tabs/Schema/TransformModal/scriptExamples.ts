export type ScriptExample = {
  id: "filter" | "transform";
  label: string;
  description: string;
  code: string;
};

function exampleVariable(name: string): string {
  const lower = name.toLowerCase().replace(/\W/g, "_");
  if (/^[a-z_][a-z0-9_]*$/.test(lower)) return lower;
  return "value";
}

function pickField(
  names: string[],
  candidates: string[],
  fallback: string,
): string {
  for (const candidate of candidates) {
    if (names.includes(candidate)) return candidate;
  }
  return names[0] || fallback;
}

export function buildScriptExamples(
  sourceNames: string[],
  targetNames: string[],
): ScriptExample[] {
  const filterKey = pickField(
    sourceNames,
    ["BUKRS", "KOKRS", "WERKS"],
    "BUKRS",
  );
  const filterVar = exampleVariable(filterKey);
  const charField = pickField(
    [...sourceNames, ...targetNames],
    ["BELNR", "HKONT", "XBLNR", "SGTXT"],
    targetNames[0] || sourceNames[0] || "BELNR",
  );
  const amountField = pickField(
    sourceNames,
    ["DMSOL", "DMBTR", "WRSHB", "DMSHB", "WRSOL", "DMHAB", "BETRW"],
    sourceNames[1] || sourceNames[0] || "DMBTR",
  );

  return [
    {
      id: "filter",
      label: "Filter rows",
      description: `Keep rows where ${filterKey} equals a value. For several values use .isin(["A", "B"]).`,
      code: [
        `${filterVar} = __source_df["${filterKey}"].astype("string").str.rstrip(" ")`,
        `__target_df = __source_df.loc[${filterVar} == "IN01"].copy()`,
      ].join("\n"),
    },
    {
      id: "transform",
      label: "Transform columns",
      description: `Strip text and convert a number. Dates: pd.to_datetime(..., format="%Y%m%d").`,
      code: [
        `__target_df["${charField}"] = __source_df["${charField}"].astype("string").str.rstrip(" ")`,
        `__target_df["${amountField}"] = pd.to_numeric(__source_df["${amountField}"], errors="coerce")`,
      ].join("\n"),
    },
  ];
}
