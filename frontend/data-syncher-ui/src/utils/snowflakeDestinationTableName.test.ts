import { describe, expect, it } from "vitest";

import {
  extractJsonTargetObjectname,
  isValidSnowflakeDestinationName,
  normalizeDestinationTableName,
  resolveDestinationTableName,
} from "./snowflakeDestinationTableName";

const mapping = {
  source: { objectname: "0FI_GL_4" },
  target: { objectname: "/IMO/CMFIGL04" },
};

describe("normalizeDestinationTableName", () => {
  it("normalises /IMO/CMFIGL04", () => {
    expect(normalizeDestinationTableName("/IMO/CMFIGL04")).toEqual({
      name: "IMO_CMFIGL04",
      warnings: [],
    });
  });

  it("prefixes a leading digit", () => {
    expect(normalizeDestinationTableName("9LEDGER").name).toBe("T_9LEDGER");
  });

  it("collapses spaces", () => {
    expect(normalizeDestinationTableName("  IMO  CM  ").name).toBe("IMO_CM");
  });

  it("strips unicode and empty results", () => {
    expect(normalizeDestinationTableName("café").name).toBe("CAF");
    const empty = normalizeDestinationTableName("///");
    expect(empty.name).toBe("");
    expect(empty.warnings.length).toBeGreaterThan(0);
  });

  it("truncates to 255 and suffixes reserved words", () => {
    expect(normalizeDestinationTableName("A".repeat(300)).name).toHaveLength(255);
    const reserved = normalizeDestinationTableName("SELECT");
    expect(reserved.name).toBe("SELECT_T");
    expect(reserved.warnings.length).toBeGreaterThan(0);
  });
});

describe("resolveDestinationTableName precedence", () => {
  const base = {
    serviceName: "ZFI_GL_4_SRV",
    entityName: "EntityOf0FI_GL_4",
    tableName: "EntityOf0FI_GL_4",
  };

  it("uses JSON over default", () => {
    const resolved = resolveDestinationTableName({
      ...base,
      mappingJson: mapping,
    });
    expect(resolved.destinationTableName).toBe("IMO_CMFIGL04");
    expect(resolved.jsonTableName).toBe("/IMO/CMFIGL04");
    expect(resolved.destinationNameSource).toBe("JSON");
  });

  it("uses MANUAL over JSON", () => {
    const resolved = resolveDestinationTableName({
      ...base,
      mappingJson: mapping,
      destinationNameSource: "MANUAL",
      manualDestinationTableName: "MY_LEDGER",
    });
    expect(resolved.destinationTableName).toBe("MY_LEDGER");
    expect(resolved.destinationNameSource).toBe("MANUAL");
  });

  it("keeps the default when no JSON is attached", () => {
    const resolved = resolveDestinationTableName({
      ...base,
      mappingJson: null,
    });
    expect(resolved.destinationNameSource).toBe("DEFAULT");
    expect(resolved.jsonTableName).toBeNull();
    expect(resolved.destinationTableName).toBeTruthy();
    expect(resolved.destinationTableName).not.toBe("IMO_CMFIGL04");
  });

  it("extracts target.objectname", () => {
    expect(extractJsonTargetObjectname(mapping)).toBe("/IMO/CMFIGL04");
    expect(isValidSnowflakeDestinationName("IMO_CMFIGL04")).toBe(true);
  });
});
