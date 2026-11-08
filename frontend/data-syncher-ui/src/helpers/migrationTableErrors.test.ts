import { describe, expect, it } from "vitest";

import {
  isGenericTableFailureMessage,
  isTableRowFailed,
  resolveTableDisplayError,
  resolveTableRowDisplayError,
} from "./migrationTableErrors";

describe("migrationTableErrors", () => {
  it("detects failed rows from status fields", () => {
    expect(isTableRowFailed({ status: "failed" })).toBe(true);
    expect(isTableRowFailed({ status_icon: "failed" })).toBe(true);
    expect(isTableRowFailed({ status: "completed" })).toBe(false);
  });

  it("prefers detailed errors over generic stubs", () => {
    const detail =
      "An error occurred while processing the table 't1': Invalid access token.";
    expect(
      resolveTableDisplayError({
        error_message: "Tables failed: ['t1']",
        message: detail,
      }),
    ).toBe(detail);
  });

  it("marks aggregate table failure summaries as generic", () => {
    expect(
      isGenericTableFailureMessage("Tables failed: ['accountingdistribution']"),
    ).toBe(true);
    expect(isGenericTableFailureMessage(detailTokenMessage())).toBe(false);
  });

  it("uses node fallback when row error is generic", () => {
    const nodeError =
      "Databricks ADLS setup failed: PERMISSION_DENIED on storage credential.";
    expect(
      resolveTableRowDisplayError(
        { error_message: "Tables failed: ['Budget__c']" },
        nodeError,
      ),
    ).toBe(nodeError);
  });
});

function detailTokenMessage() {
  return "Databricks SQL warehouse request failed: Invalid access token.";
}
