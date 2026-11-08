import { describe, expect, it } from "vitest";

import { resolveDestinationSchema } from "./resolveDestinationSchema";

describe("resolveDestinationSchema", () => {
  it("prefers the destination_schema form field", () => {
    expect(
      resolveDestinationSchema(
        {
          destination_schema: "LANDING",
          connection_name: "My Databricks Prod",
        },
        "PUBLIC",
      ),
    ).toBe("LANDING");
  });

  it("falls back when the form field is empty", () => {
    expect(
      resolveDestinationSchema({ destination_schema: "  " }, "BRONZE"),
    ).toBe("BRONZE");
  });

  it("returns empty string when neither value is set", () => {
    expect(resolveDestinationSchema({})).toBe("");
  });
});
