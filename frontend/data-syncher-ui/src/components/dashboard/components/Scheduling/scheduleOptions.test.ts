import { describe, expect, it } from "vitest";

import {
  type DayCode,
  applyRecurrencePattern,
  batchScheduleLabel,
  clampCustomMinutes,
  dateFnsPatternForTimezone,
  dateOrderHintForTimezone,
  defaultScheduleValue,
  displayToIsoDate,
  formatNextSyncLabel,
  formatRecurrenceSummary,
  formatShortDate,
  frequencyLabel,
  fromPipelineSchedule,
  getBrowserTimezone,
  intervalValueToMinutes,
  isPresetFrequency,
  isValidIanaTimezone,
  isoDateToDisplay,
  localeForTimezone,
  mergeSyncStartDate,
  monthlyRelativeCronDay,
  parseCustomMinutes,
  parseMonthlyRelativeCronDay,
  pipelineScheduleLabel,
  splitSyncStartDate,
  startNodeScheduleLabels,
  toApiSchedule,
  weeklySelectionValid,
} from "./scheduleOptions";

const baseConfig = {
  cron_type: "weekdays" as const,
  time: "09:00",
  timezone: "UTC",
  day_of_week: "mon",
  days_of_week: ["mon"] as DayCode[],
  day: 1,
  interval_unit: "minute" as const,
  interval_value: 15,
  recurrence_pattern: "every_n_minutes" as const,
  monthly_mode: "day_of_month" as const,
  monthly_week: "first" as const,
  monthly_weekday: "fri" as const,
};

const baseValue = {
  recurring: true,
  time_frequency: 15,
  execution_order: "parallel" as const,
  sync_start_date: null,
  sync_end_date: null,
  end_mode: "never" as const,
  schedule_type: "interval" as const,
  schedule_config: {
    ...baseConfig,
    days_of_week: ["mon"] as DayCode[],
  },
};

describe("scheduleOptions", () => {
  describe("custom minutes", () => {
    it("detects preset frequencies", () => {
      expect(isPresetFrequency(15)).toBe(true);
      expect(isPresetFrequency(42)).toBe(false);
    });

    it("clamps custom minutes", () => {
      expect(clampCustomMinutes(0)).toBe(1);
      expect(clampCustomMinutes(99999)).toBe(10080);
      expect(clampCustomMinutes(47.8)).toBe(48);
    });

    it("parses custom minute strings", () => {
      expect(parseCustomMinutes("42")).toBe(42);
      expect(parseCustomMinutes("")).toBeNull();
      expect(parseCustomMinutes("abc")).toBeNull();
    });

    it("converts interval units to minutes", () => {
      expect(intervalValueToMinutes("hour", 2)).toBe(120);
      expect(intervalValueToMinutes("week", 2)).toBe(20160);
    });
  });

  describe("toApiSchedule manual", () => {
    it("serializes manual schedule", () => {
      const payload = toApiSchedule({
        ...baseValue,
        recurring: false,
        schedule_type: "manual",
      });
      expect(payload.schedule_type).toBe("manual");
      expect(payload.schedule_config).toEqual({});
    });
  });

  describe("toApiSchedule interval", () => {
    it.each([
      [5, "5"],
      [15, "15"],
      [42, "42"],
      [360, "360"],
    ])("serializes %i minutes", (minutes, expected) => {
      const payload = toApiSchedule({
        ...baseValue,
        time_frequency: minutes,
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          interval_unit: "minute",
          interval_value: minutes,
        },
      });
      expect(payload.schedule_type).toBe("interval");
      expect(payload.time_frequency).toBe(expected);
    });

    it("serializes every 2 weeks as interval weeks", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_config: {
          ...baseConfig,
          days_of_week: ["fri"],
          recurrence_pattern: "weekly",
          interval_unit: "week",
          interval_value: 2,
        },
      });
      expect(payload.schedule_type).toBe("interval");
      expect(payload.schedule_config).toMatchObject({
        interval_unit: "week",
        interval_value: 2,
      });
      expect(payload.time_frequency).toBe("20160");
    });
  });

  describe("toApiSchedule cron", () => {
    it("serializes daily cron", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          recurrence_pattern: "daily",
          interval_unit: "day",
          interval_value: 1,
          time: "08:30",
          timezone: "Asia/Kolkata",
        },
      });
      expect(payload.schedule_type).toBe("cron");
      expect(payload.schedule_config).toMatchObject({
        cron_type: "daily",
        hour: 8,
        minute: 30,
        timezone: "Asia/Kolkata",
      });
    });

    it("serializes weekdays cron", () => {
      const payload = toApiSchedule({
        ...baseValue,
        execution_order: "sequential",
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon", "tue", "wed", "thu", "fri"],
          recurrence_pattern: "weekdays",
          interval_unit: "day",
          interval_value: 1,
        },
      });
      expect(
        (payload.schedule_config as Record<string, unknown>).cron_type,
      ).toBe("weekdays");
    });

    it("serializes weekly cron with multiple days", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon", "wed", "fri"],
          recurrence_pattern: "weekly",
          interval_unit: "week",
          interval_value: 1,
          time: "10:15",
        },
      });
      expect(payload.schedule_config).toMatchObject({
        cron_type: "weekly",
        days_of_week: ["mon", "wed", "fri"],
        day_of_week: "mon",
        hour: 10,
        minute: 15,
      });
    });

    it("serializes monthly cron with day of month", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          recurrence_pattern: "monthly",
          monthly_mode: "day_of_month",
          interval_unit: "month",
          interval_value: 1,
          time: "07:00",
          day: 15,
        },
      });
      expect(payload.schedule_config).toMatchObject({
        cron_type: "monthly",
        monthly_mode: "day_of_month",
        day: 15,
        hour: 7,
        minute: 0,
      });
    });

    it("serializes monthly relative cron (first Friday)", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["fri"],
          recurrence_pattern: "monthly",
          monthly_mode: "relative",
          monthly_week: "first",
          monthly_weekday: "fri",
          interval_unit: "month",
          interval_value: 1,
          time: "16:00",
        },
      });
      expect(payload.schedule_config).toMatchObject({
        cron_type: "monthly",
        monthly_mode: "relative",
        monthly_day_cron: "1st fri",
        day: "1st fri",
      });
    });
  });

  describe("monthly helpers", () => {
    it("builds and parses relative monthly cron day", () => {
      expect(monthlyRelativeCronDay("first", "fri")).toBe("1st fri");
      expect(parseMonthlyRelativeCronDay("1st fri")).toEqual({
        week: "first",
        weekday: "fri",
      });
      expect(parseMonthlyRelativeCronDay("last mon")).toEqual({
        week: "last",
        weekday: "mon",
      });
    });

    it("applyRecurrencePattern monthly sets relative defaults", () => {
      const next = applyRecurrencePattern(defaultScheduleValue(), "monthly");
      expect(next.schedule_config.recurrence_pattern).toBe("monthly");
      expect(next.schedule_config.monthly_mode).toBe("day_of_month");
    });
  });

  describe("fromPipelineSchedule", () => {
    it("loads manual schedule", () => {
      const value = fromPipelineSchedule({ schedule_type: "manual" });
      expect(value.recurring).toBe(false);
      expect(value.schedule_type).toBe("manual");
    });

    it("loads multi-day weekly cron", () => {
      const value = fromPipelineSchedule({
        schedule_type: "cron",
        schedule_config: {
          cron_type: "weekly",
          hour: 16,
          minute: 0,
          days_of_week: ["fri"],
          recurrence_pattern: "weekly",
          timezone: "UTC",
        },
      });
      expect(value.schedule_config.interval_unit).toBe("week");
      expect(value.schedule_config.days_of_week).toEqual(["fri"]);
    });
  });

  describe("labels and summary", () => {
    it("labels custom interval minutes", () => {
      expect(frequencyLabel(42)).toBe("42 min");
      expect(frequencyLabel(360)).toBe("6 hours");
    });

    it("labels pipeline interval from API shape", () => {
      const value = fromPipelineSchedule({
        schedule_type: "interval",
        time_frequency: "42",
      });
      expect(value.time_frequency).toBe(42);
      expect(
        batchScheduleLabel({ schedule_type: "interval", time_frequency: 42 }),
      ).toBe("42 min");
    });

    it("labels weekly cron with multiple days", () => {
      expect(
        batchScheduleLabel({
          schedule_type: "cron",
          schedule_config: {
            cron_type: "weekly",
            hour: 9,
            minute: 0,
            days_of_week: ["mon", "wed"],
            timezone: "UTC",
          },
        }),
      ).toBe("Weekly (mon, wed) 09:00 UTC");
    });

    it("formats recurrence summary with until date", () => {
      const summary = formatRecurrenceSummary({
        ...baseValue,
        sync_start_date: "2026-07-03T16:00:00",
        sync_end_date: "2026-12-25T23:59:59",
        end_mode: "on_date",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["fri"],
          recurrence_pattern: "weekly",
          interval_unit: "week",
          interval_value: 1,
          time: "16:00",
        },
      });
      expect(summary).toContain("Occurs every Friday at 16:00");
      expect(summary).toContain("until");
    });

    it("formats next sync label", () => {
      expect(formatNextSyncLabel("2026-07-02T14:30:00Z", "active")).toMatch(
        /^Next: /,
      );
      expect(formatNextSyncLabel(null, "paused")).toBe("Paused");
      expect(formatNextSyncLabel(null, "active", true)).toBe("Schedule paused");
      expect(formatNextSyncLabel(null, "active")).toBeNull();
    });

    it("builds start node schedule labels", () => {
      const labels = startNodeScheduleLabels({
        readable_schedule: "Every 30 min",
        status: "active",
        next_run_at: "2026-07-02T14:30:00Z",
      });
      expect(labels.scheduleLabel).toBe("Every 30 min");
      expect(labels.nextSyncLabel).toMatch(/^Next: /);
    });

    it("marks manual next sync label", () => {
      const labels = startNodeScheduleLabels({
        schedule_type: "manual",
        readable_schedule: "Manual only",
        status: "active",
      });
      expect(labels.nextSyncLabel).toBe("Manual");
    });

    it("shows Paused instead of cadence when schedule is paused", () => {
      expect(
        pipelineScheduleLabel({
          readable_schedule: "1 hour",
          schedule_paused: true,
        }),
      ).toBe("Paused");
      expect(
        pipelineScheduleLabel({
          readable_schedule: "1 hour",
          schedule_paused: false,
        }),
      ).toBe("1 hour");
    });
  });

  describe("timezone and dates", () => {
    it("defaults new schedules to a valid IANA browser timezone", () => {
      const tz = defaultScheduleValue().schedule_config.timezone;
      expect(tz).toBe(getBrowserTimezone());
      expect(isValidIanaTimezone(tz)).toBe(true);
    });

    it("validates IANA timezone identifiers", () => {
      expect(isValidIanaTimezone("Asia/Kolkata")).toBe(true);
      expect(isValidIanaTimezone("Asia/Calcutta")).toBe(true);
      expect(isValidIanaTimezone("Not/A_Real_Zone")).toBe(false);
    });

    it("derives locale and date order from timezone", () => {
      expect(localeForTimezone("Asia/Kolkata")).toBeTruthy();
      expect(dateOrderHintForTimezone("Asia/Kolkata")).toMatch(
        /(dd|mm).*(dd|mm).*yyyy/i,
      );
      expect(dateOrderHintForTimezone("UTC")).toMatch(/yyyy/i);
    });

    it("formats summary dates in the selected schedule timezone", () => {
      const summary = formatRecurrenceSummary({
        ...baseValue,
        sync_start_date: "2026-07-03T00:00:00",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          recurrence_pattern: "daily",
          interval_unit: "day",
          interval_value: 1,
          time: "09:00",
          timezone: "Asia/Kolkata",
        },
      });
      expect(summary).toContain("Asia/Kolkata");
      expect(summary).toContain("effective");
    });

    it("formats short dates with optional timezone", () => {
      const formatted = formatShortDate("2026-07-07", "Asia/Kolkata");
      expect(formatted).toBeTruthy();
      expect(formatShortDate(null, "UTC")).toBeNull();
    });

    it("round-trips start date and time fields", () => {
      const merged = mergeSyncStartDate("2026-07-07", "09:30");
      expect(merged).toBe("2026-07-07T09:30");
      const split = splitSyncStartDate(merged, "09:00");
      expect(split.date).toBe("2026-07-07");
      expect(split.time).toBe("09:30");
    });

    it("formats and parses dates per timezone locale pattern", () => {
      const iso = "2026-03-15";
      const usPattern = dateFnsPatternForTimezone("America/New_York");
      const dePattern = dateFnsPatternForTimezone("Europe/Berlin");
      expect(usPattern).toMatch(/MM.*dd.*yyyy/);
      expect(dePattern).toMatch(/dd.*MM.*yyyy/);

      const usDisplay = isoDateToDisplay(iso, "America/New_York");
      expect(usDisplay).toBe("03/15/2026");
      expect(displayToIsoDate(usDisplay, "America/New_York")).toBe(iso);

      const deDisplay = isoDateToDisplay(iso, "Europe/Berlin");
      expect(deDisplay).toBe("15.03.2026");
      expect(displayToIsoDate(deDisplay, "Europe/Berlin")).toBe(iso);

      expect(isoDateToDisplay(iso, "Asia/Kolkata")).toBeTruthy();
      expect(
        displayToIsoDate(isoDateToDisplay(iso, "Asia/Kolkata"), "Asia/Kolkata"),
      ).toBe(iso);
    });

    it("reformats display when timezone changes (same ISO date)", () => {
      const iso = "2026-03-15";
      const us = isoDateToDisplay(iso, "America/New_York");
      const de = isoDateToDisplay(iso, "Europe/Berlin");
      expect(us).toBe("03/15/2026");
      expect(de).toBe("15.03.2026");
      expect(displayToIsoDate(us, "America/New_York")).toBe(iso);
      expect(displayToIsoDate(de, "Europe/Berlin")).toBe(iso);
    });

    it("serializes hourly interval without cron timezone (interval trigger)", () => {
      const payload = toApiSchedule({
        ...baseValue,
        time_frequency: 60,
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          recurrence_pattern: "every_n_minutes",
          interval_unit: "hour",
          interval_value: 1,
          timezone: "Asia/Kolkata",
        },
      });
      expect(payload.schedule_type).toBe("interval");
      expect(payload.time_frequency).toBe("60");
      expect(payload.schedule_config).not.toHaveProperty("timezone");
    });

    it("serializes daily cron with timezone for APScheduler", () => {
      const payload = toApiSchedule({
        ...baseValue,
        schedule_type: "cron",
        schedule_config: {
          ...baseConfig,
          days_of_week: ["mon"],
          recurrence_pattern: "daily",
          interval_unit: "day",
          interval_value: 1,
          time: "09:00",
          timezone: "Asia/Kolkata",
        },
      });
      expect(payload.schedule_type).toBe("cron");
      expect(payload.schedule_config).toMatchObject({
        cron_type: "daily",
        hour: 9,
        minute: 0,
        timezone: "Asia/Kolkata",
      });
    });

    it("restores saved timezone from pipeline cron config", () => {
      const value = fromPipelineSchedule({
        schedule_type: "cron",
        time_frequency: "1440",
        schedule_config: {
          cron_type: "daily",
          hour: 8,
          minute: 0,
          timezone: "Europe/London",
        },
      });
      expect(value.schedule_config.timezone).toBe("Europe/London");
      expect(value.schedule_config.recurrence_pattern).toBe("daily");
    });
  });

  describe("weeklySelectionValid", () => {
    it("requires at least one day for weekly", () => {
      expect(
        weeklySelectionValid({
          ...baseValue,
          schedule_config: {
            ...baseConfig,
            days_of_week: [],
            recurrence_pattern: "weekly",
            interval_unit: "week",
            interval_value: 1,
          },
        }),
      ).toBe(false);
      expect(
        weeklySelectionValid({
          ...baseValue,
          schedule_config: {
            ...baseConfig,
            days_of_week: ["fri"],
            recurrence_pattern: "weekly",
            interval_unit: "week",
            interval_value: 1,
          },
        }),
      ).toBe(true);
    });
  });
});
