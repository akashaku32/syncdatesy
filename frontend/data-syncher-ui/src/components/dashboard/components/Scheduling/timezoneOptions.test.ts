import { describe, expect, it } from "vitest";

import {
  filterTimezonesForSearch,
  formatTimezoneLabel,
} from "./timezoneOptions";

describe("timezoneOptions search", () => {
  it("finds Asia/Kolkata by city name or legacy Calcutta alias", () => {
    const byKolkata = filterTimezonesForSearch("kolkata");
    expect(byKolkata.some((tz) => tz.iana === "Asia/Kolkata")).toBe(true);

    const byCalcutta = filterTimezonesForSearch("calcutta");
    expect(byCalcutta.some((tz) => tz.iana === "Asia/Kolkata")).toBe(true);
  });

  it("finds zones by UTC offset label", () => {
    const matches = filterTimezonesForSearch("+05:30");
    expect(matches.some((tz) => tz.iana === "Asia/Kolkata")).toBe(true);
    expect(matches[0].label).toContain(formatTimezoneLabel("Asia/Kolkata"));
  });

  it("returns empty for blank query", () => {
    expect(filterTimezonesForSearch("   ")).toEqual([]);
  });
});
