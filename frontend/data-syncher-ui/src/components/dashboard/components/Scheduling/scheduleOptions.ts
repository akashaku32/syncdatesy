import { format, isValid, parse } from "date-fns";

import { type BatchScheduleConfig } from "@/types/connectors";

export type FrequencyOption = {
  value: number;
  label: string;
};

export const FREQUENCY_OPTIONS: FrequencyOption[] = [
  { value: 5, label: "5 min" },
  { value: 15, label: "15 min" },
  { value: 30, label: "30 min" },
  { value: 60, label: "1 hour" },
  { value: 360, label: "6 hours" },
  { value: 1440, label: "Daily" },
];

export const QUICK_PRESET_OPTIONS: FrequencyOption[] = [
  { value: 5, label: "5 min" },
  { value: 15, label: "15 min" },
  { value: 30, label: "30 min" },
  { value: 60, label: "1 hour" },
];

export const CUSTOM_MINUTES_MIN = 1;
export const CUSTOM_MINUTES_MAX = 10080; // 7 days

export const DAY_CODES = [
  "mon",
  "tue",
  "wed",
  "thu",
  "fri",
  "sat",
  "sun",
] as const;

export type DayCode = (typeof DAY_CODES)[number];

export const DAY_CHIP_LABELS = ["M", "T", "W", "T", "F", "S", "S"] as const;

export const WEEKDAY_CODES: DayCode[] = ["mon", "tue", "wed", "thu", "fri"];

export type IntervalUnit = "minute" | "hour" | "day" | "week" | "month";

export type RecurrencePattern =
  | "every_n_minutes"
  | "daily"
  | "weekdays"
  | "weekly"
  | "monthly";

export type MonthlyMode = "day_of_month" | "relative";

export type MonthlyWeekOccurrence =
  | "first"
  | "second"
  | "third"
  | "fourth"
  | "last";

export const MONTHLY_WEEK_TO_CRON: Record<MonthlyWeekOccurrence, string> = {
  first: "1st",
  second: "2nd",
  third: "3rd",
  fourth: "4th",
  last: "last",
};

export const CRON_TO_MONTHLY_WEEK: Record<string, MonthlyWeekOccurrence> = {
  "1st": "first",
  "2nd": "second",
  "3rd": "third",
  "4th": "fourth",
  last: "last",
};

export type CronType = "daily" | "weekdays" | "weekly" | "monthly";

export const isPresetFrequency = (minutes: number): boolean =>
  FREQUENCY_OPTIONS.some((opt) => opt.value === minutes);

export const clampCustomMinutes = (raw: number): number => {
  if (!Number.isFinite(raw)) return CUSTOM_MINUTES_MIN;
  return Math.min(
    CUSTOM_MINUTES_MAX,
    Math.max(CUSTOM_MINUTES_MIN, Math.round(raw)),
  );
};

export const parseCustomMinutes = (raw: string): number | null => {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return null;
  return clampCustomMinutes(value);
};

export const intervalValueToMinutes = (
  unit: IntervalUnit,
  value: number,
): number => {
  const n = Math.max(1, Math.round(value));
  switch (unit) {
    case "minute":
      return clampCustomMinutes(n);
    case "hour":
      return clampCustomMinutes(n * 60);
    case "day":
      return n * 1440;
    case "week":
      return n * 7 * 1440;
    case "month":
      return n * 30 * 1440;
    default:
      return clampCustomMinutes(n);
  }
};

export const minutesToIntervalParts = (
  minutes: number,
): { unit: IntervalUnit; value: number } => {
  const m = clampCustomMinutes(minutes);
  if (m % 10080 === 0) {
    const weeks = m / 10080;
    return { unit: "week", value: weeks };
  }
  if (m % 1440 === 0) {
    const days = m / 1440;
    return { unit: "day", value: days };
  }
  if (m % 60 === 0) {
    const hours = m / 60;
    return { unit: "hour", value: hours };
  }
  return { unit: "minute", value: m };
};

export const frequencyLabel = (minutes: number | string): string => {
  const value = Number(minutes);
  const match = FREQUENCY_OPTIONS.find((opt) => opt.value === value);
  if (match) return match.label;
  if (!Number.isFinite(value)) return String(minutes);
  if (value % 1440 === 0) {
    const days = value / 1440;
    return days === 1 ? "Daily" : `Every ${days} days`;
  }
  if (value % 60 === 0) {
    const hours = value / 60;
    return hours === 1 ? "1 hour" : `Every ${hours} hours`;
  }
  return `${value} min`;
};

export const isWeekdaysSet = (days: string[]): boolean => {
  if (days.length !== 5) return false;
  const sorted = [...days].sort().join(",");
  return sorted === WEEKDAY_CODES.slice().sort().join(",");
};

export const dayCodeLabel = (code: string): string => {
  const labels: Record<string, string> = {
    mon: "Monday",
    tue: "Tuesday",
    wed: "Wednesday",
    thu: "Thursday",
    fri: "Friday",
    sat: "Saturday",
    sun: "Sunday",
  };
  return labels[code] ?? code;
};

export const formatShortDate = (
  raw: string | null | undefined,
  timezone?: string,
): string | null => {
  if (!raw) return null;
  const parsed = new Date(raw.includes("T") ? raw : `${raw}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return null;
  const options: Intl.DateTimeFormatOptions = {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  };
  if (timezone) {
    options.timeZone = timezone;
  }
  return parsed.toLocaleDateString(
    timezone ? localeForTimezone(timezone) : undefined,
    options,
  );
};

export interface ScheduleConfigDraft {
  cron_type: CronType;
  time: string;
  timezone: string;
  day_of_week: string;
  days_of_week: DayCode[];
  day: number;
  interval_unit: IntervalUnit;
  interval_value: number;
  recurrence_pattern: RecurrencePattern;
  monthly_mode: MonthlyMode;
  monthly_week: MonthlyWeekOccurrence;
  monthly_weekday: DayCode;
}

export interface ScheduleValue {
  recurring: boolean;
  time_frequency: number;
  execution_order: "parallel" | "sequential";
  sync_start_date: string | null;
  sync_end_date: string | null;
  end_mode: "never" | "on_date";
  schedule_type: "manual" | "interval" | "cron";
  schedule_config: ScheduleConfigDraft;
}

const defaultTimezone = (): string => getBrowserTimezone();

export const getBrowserTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

export const isValidIanaTimezone = (timezone: string): boolean => {
  const tz = timezone?.trim();
  if (!tz) return false;
  try {
    Intl.DateTimeFormat(undefined, { timeZone: tz });
    return true;
  } catch {
    return false;
  }
};

export const localeForTimezone = (timezone: string): string => {
  const tz = timezone?.trim();
  if (!tz) return "en-US";

  const explicit: Record<string, string> = {
    UTC: "en-GB",
    "America/New_York": "en-US",
    "America/Chicago": "en-US",
    "America/Denver": "en-US",
    "America/Los_Angeles": "en-US",
    "America/Phoenix": "en-US",
    "America/Toronto": "en-CA",
    "America/Vancouver": "en-CA",
    "America/Mexico_City": "es-MX",
    "America/Sao_Paulo": "pt-BR",
    "Europe/London": "en-GB",
    "Europe/Dublin": "en-IE",
    "Europe/Berlin": "de-DE",
    "Europe/Paris": "fr-FR",
    "Europe/Madrid": "es-ES",
    "Europe/Rome": "it-IT",
    "Europe/Amsterdam": "nl-NL",
    "Europe/Stockholm": "sv-SE",
    "Europe/Warsaw": "pl-PL",
    "Asia/Kolkata": "en-IN",
    "Asia/Singapore": "en-SG",
    "Asia/Tokyo": "ja-JP",
    "Asia/Shanghai": "zh-CN",
    "Asia/Seoul": "ko-KR",
    "Australia/Sydney": "en-AU",
    "Pacific/Auckland": "en-NZ",
  };
  if (explicit[tz]) return explicit[tz];

  const city = tz.split("/").pop() ?? "";
  const europeCities: Record<string, string> = {
    Berlin: "de-DE",
    Vienna: "de-AT",
    Zurich: "de-CH",
    Paris: "fr-FR",
    London: "en-GB",
    Dublin: "en-IE",
    Madrid: "es-ES",
    Rome: "it-IT",
    Amsterdam: "nl-NL",
    Brussels: "nl-BE",
    Stockholm: "sv-SE",
    Oslo: "nb-NO",
    Copenhagen: "da-DK",
    Helsinki: "fi-FI",
    Warsaw: "pl-PL",
    Prague: "cs-CZ",
    Budapest: "hu-HU",
    Athens: "el-GR",
    Lisbon: "pt-PT",
    Istanbul: "tr-TR",
    Moscow: "ru-RU",
    Kyiv: "uk-UA",
  };
  const americaCities: Record<string, string> = {
    Mexico_City: "es-MX",
    Sao_Paulo: "pt-BR",
    Buenos_Aires: "es-AR",
    Toronto: "en-CA",
    Vancouver: "en-CA",
  };
  const asiaCities: Record<string, string> = {
    Kolkata: "en-IN",
    Tokyo: "ja-JP",
    Shanghai: "zh-CN",
    Singapore: "en-SG",
    Seoul: "ko-KR",
    Dubai: "ar-AE",
  };

  try {
    if (tz.startsWith("Europe/")) return europeCities[city] ?? "en-GB";
    if (tz.startsWith("America/")) return americaCities[city] ?? "en-US";
    if (tz.startsWith("Asia/")) return asiaCities[city] ?? "en-IN";
    if (tz.startsWith("Australia/") || tz.startsWith("Pacific/"))
      return "en-AU";
    if (tz.startsWith("Africa/")) return "en-ZA";
    Intl.DateTimeFormat(undefined, { timeZone: tz });
    return "en-US";
  } catch {
    return "en-US";
  }
};

const SAMPLE_DATE_FOR_FORMAT = new Date(Date.UTC(2026, 6, 7, 12, 0, 0));

/** Example date order for the schedule timezone (e.g. dd/mm/yyyy). */
export const dateOrderHintForTimezone = (timezone: string): string => {
  try {
    const parts = new Intl.DateTimeFormat(localeForTimezone(timezone), {
      timeZone: timezone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).formatToParts(SAMPLE_DATE_FOR_FORMAT);
    return parts
      .map((part) => {
        if (part.type === "day") return "dd";
        if (part.type === "month") return "mm";
        if (part.type === "year") return "yyyy";
        return part.type === "literal" ? part.value : "";
      })
      .join("");
  } catch {
    return "yyyy-mm-dd";
  }
};

/** date-fns format pattern for the schedule timezone (e.g. dd/MM/yyyy, MM/dd/yyyy). */
export const dateFnsPatternForTimezone = (timezone: string): string => {
  try {
    const parts = new Intl.DateTimeFormat(localeForTimezone(timezone), {
      timeZone: timezone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).formatToParts(SAMPLE_DATE_FOR_FORMAT);
    return parts
      .map((part) => {
        if (part.type === "day") return "dd";
        if (part.type === "month") return "MM";
        if (part.type === "year") return "yyyy";
        return part.type === "literal" ? part.value : "";
      })
      .join("");
  } catch {
    return "yyyy-MM-dd";
  }
};

export const isoDateToDisplay = (isoDate: string, timezone: string): string => {
  if (!isoDate || !/^\d{4}-\d{2}-\d{2}/.test(isoDate)) return "";
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  const date = new Date(y, m - 1, d);
  if (Number.isNaN(date.getTime())) return "";
  return format(date, dateFnsPatternForTimezone(timezone));
};

export const displayToIsoDate = (
  display: string,
  timezone: string,
): string | null => {
  const trimmed = display.trim();
  if (!trimmed) return null;
  const pattern = dateFnsPatternForTimezone(timezone);
  try {
    const parsed = parse(trimmed, pattern, new Date());
    if (!isValid(parsed)) return null;
    return format(parsed, "yyyy-MM-dd");
  } catch {
    return null;
  }
};

export const DEFAULT_SCHEDULE_CONFIG: ScheduleConfigDraft = {
  cron_type: "weekdays",
  time: "09:00",
  timezone: defaultTimezone(),
  day_of_week: "mon",
  days_of_week: ["mon"],
  day: 1,
  interval_unit: "minute",
  interval_value: 15,
  recurrence_pattern: "every_n_minutes",
  monthly_mode: "day_of_month",
  monthly_week: "first",
  monthly_weekday: "fri",
};

export const RECURRENCE_PATTERN_OPTIONS: {
  value: RecurrencePattern;
  label: string;
}[] = [
  { value: "every_n_minutes", label: "Every few minutes / hours" },
  { value: "daily", label: "Daily" },
  { value: "weekdays", label: "Every weekday (Mon–Fri)" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

export const monthlyRelativeCronDay = (
  week: MonthlyWeekOccurrence,
  weekday: DayCode,
): string => {
  if (week === "last") return `last ${weekday}`;
  return `${MONTHLY_WEEK_TO_CRON[week]} ${weekday}`;
};

export const parseMonthlyRelativeCronDay = (
  raw: string,
): { week: MonthlyWeekOccurrence; weekday: DayCode } | null => {
  const trimmed = raw.trim().toLowerCase();
  const lastMatch = trimmed.match(/^last\s+(mon|tue|wed|thu|fri|sat|sun)$/);
  if (lastMatch) {
    return { week: "last", weekday: lastMatch[1] as DayCode };
  }
  const match = trimmed.match(
    /^(1st|2nd|3rd|4th)\s+(mon|tue|wed|thu|fri|sat|sun)$/,
  );
  if (match) {
    return {
      week: CRON_TO_MONTHLY_WEEK[match[1]] ?? "first",
      weekday: match[2] as DayCode,
    };
  }
  return null;
};

export const defaultScheduleValue = (): ScheduleValue => ({
  recurring: true,
  time_frequency: 15,
  execution_order: "parallel",
  sync_start_date: null,
  sync_end_date: null,
  end_mode: "never",
  schedule_type: "interval",
  schedule_config: { ...DEFAULT_SCHEDULE_CONFIG },
});

const parseDaysOfWeek = (cfg: Record<string, unknown>): DayCode[] => {
  const days = cfg.days_of_week;
  if (Array.isArray(days) && days.length > 0) {
    return days.map((d) => String(d)) as DayCode[];
  }
  const legacy = String(cfg.day_of_week ?? "mon");
  if (legacy.includes(",")) {
    return legacy.split(",").map((d) => d.trim()) as DayCode[];
  }
  return [legacy as DayCode];
};

const inferRecurrencePattern = (
  scheduleType: string,
  cfg: Record<string, unknown>,
  unit: IntervalUnit,
): RecurrencePattern => {
  const stored = String(cfg.recurrence_pattern ?? "");
  if (
    stored === "every_n_minutes" ||
    stored === "daily" ||
    stored === "weekdays" ||
    stored === "weekly" ||
    stored === "monthly"
  ) {
    return stored as RecurrencePattern;
  }
  if (scheduleType === "interval" || unit === "minute" || unit === "hour") {
    return "every_n_minutes";
  }
  const cronType = String(cfg.cron_type ?? "weekdays");
  if (cronType === "daily") return "daily";
  if (cronType === "weekdays") return "weekdays";
  if (cronType === "weekly") return "weekly";
  return "monthly";
};

const scheduleToValue = (source: {
  time_frequency?: string | number;
  execution_order?: "parallel" | "sequential";
  sync_start_date?: string | null;
  sync_end_date?: string | null;
  schedule_type?: string;
  schedule_config?: BatchScheduleConfig | null;
}): ScheduleValue => {
  const base = defaultScheduleValue();
  const scheduleType = source.schedule_type;
  if (scheduleType === "manual") {
    return {
      ...base,
      recurring: false,
      schedule_type: "manual",
      sync_start_date: source.sync_start_date ?? null,
      sync_end_date: source.sync_end_date ?? null,
      end_mode: source.sync_end_date ? "on_date" : "never",
    };
  }

  const cfg = (source.schedule_config ?? {}) as Record<string, unknown>;
  const cronType = String(cfg.cron_type ?? "weekdays") as CronType;
  const hour = Number(cfg.hour ?? 9);
  const minute = Number(cfg.minute ?? 0);
  const daysOfWeek = parseDaysOfWeek(cfg);
  const intervalUnit = String(cfg.interval_unit ?? "") as IntervalUnit;
  const intervalValue = Number(cfg.interval_value ?? 0);
  const timeFrequency = Number(source.time_frequency) || 15;

  let unit: IntervalUnit = intervalUnit || "minute";
  let value = intervalValue || timeFrequency;

  if (scheduleType === "cron") {
    if (cronType === "daily") {
      unit = "day";
      value = 1;
    } else if (cronType === "weekdays") {
      unit = "day";
      value = 1;
    } else if (cronType === "weekly") {
      unit = "week";
      value = 1;
    } else if (cronType === "monthly") {
      unit = "month";
      value = 1;
    }
  } else if (scheduleType === "interval") {
    if (intervalUnit === "week" && intervalValue > 0) {
      unit = "week";
      value = intervalValue;
    } else {
      const parts = minutesToIntervalParts(timeFrequency);
      unit = parts.unit;
      value = parts.value;
    }
  }

  const monthlyMode = String(cfg.monthly_mode ?? "") as MonthlyMode;
  let parsedMonthlyMode: MonthlyMode = "day_of_month";
  let monthlyWeek: MonthlyWeekOccurrence = "first";
  let monthlyWeekday: DayCode = daysOfWeek[0] ?? "fri";

  if (monthlyMode === "relative") {
    parsedMonthlyMode = "relative";
    monthlyWeek =
      (String(cfg.monthly_week ?? "first") as MonthlyWeekOccurrence) || "first";
    monthlyWeekday = (String(cfg.monthly_weekday ?? "fri") as DayCode) || "fri";
  } else if (typeof cfg.monthly_day_cron === "string") {
    const parsed = parseMonthlyRelativeCronDay(String(cfg.monthly_day_cron));
    if (parsed) {
      parsedMonthlyMode = "relative";
      monthlyWeek = parsed.week;
      monthlyWeekday = parsed.weekday;
    }
  } else if (typeof cfg.day === "string") {
    const parsed = parseMonthlyRelativeCronDay(String(cfg.day));
    if (parsed) {
      parsedMonthlyMode = "relative";
      monthlyWeek = parsed.week;
      monthlyWeekday = parsed.weekday;
    }
  }

  const recurrencePattern = inferRecurrencePattern(
    scheduleType ?? "interval",
    cfg,
    unit,
  );
  const patternUnit =
    recurrencePattern === "every_n_minutes"
      ? unit === "hour"
        ? "hour"
        : "minute"
      : unit;

  return {
    recurring: true,
    time_frequency: timeFrequency,
    execution_order: source.execution_order ?? "parallel",
    sync_start_date: source.sync_start_date ?? null,
    sync_end_date: source.sync_end_date ?? null,
    end_mode: source.sync_end_date ? "on_date" : "never",
    schedule_type: scheduleType === "cron" ? "cron" : "interval",
    schedule_config: {
      ...base.schedule_config,
      cron_type: cronType,
      time: `${String(Number.isFinite(hour) ? hour : 9).padStart(2, "0")}:${String(
        Number.isFinite(minute) ? minute : 0,
      ).padStart(2, "0")}`,
      timezone: String(cfg.timezone ?? defaultTimezone()),
      day_of_week: daysOfWeek[0] ?? "mon",
      days_of_week: daysOfWeek,
      day: Number(cfg.day ?? 1),
      interval_unit: patternUnit,
      interval_value: value,
      recurrence_pattern: recurrencePattern,
      monthly_mode: parsedMonthlyMode,
      monthly_week: monthlyWeek,
      monthly_weekday: monthlyWeekday,
    },
  };
};

export const fromBatchSchedule = scheduleToValue;

export const fromPipelineSchedule = (pipeline: {
  time_frequency?: string | number;
  sync_start_date?: string | null;
  sync_end_date?: string | null;
  schedule_type?: string;
  schedule_config?: BatchScheduleConfig | null;
}): ScheduleValue => scheduleToValue(pipeline);

export const applyQuickPreset = (
  value: ScheduleValue,
  minutes: number,
): ScheduleValue => ({
  ...value,
  recurring: true,
  schedule_type: "interval",
  time_frequency: minutes,
  schedule_config: {
    ...value.schedule_config,
    recurrence_pattern: "every_n_minutes",
    interval_unit: "minute",
    interval_value: minutes,
  },
});

export const applyRecurrencePattern = (
  value: ScheduleValue,
  pattern: RecurrencePattern,
): ScheduleValue => {
  const cfg = value.schedule_config;
  const next: ScheduleConfigDraft = {
    ...cfg,
    recurrence_pattern: pattern,
  };

  if (pattern === "every_n_minutes") {
    next.interval_unit = cfg.interval_unit === "hour" ? "hour" : "minute";
    next.interval_value = cfg.interval_value || 15;
  } else if (pattern === "daily") {
    next.interval_unit = "day";
    next.interval_value = 1;
  } else if (pattern === "weekdays") {
    next.interval_unit = "week";
    next.interval_value = 1;
    next.days_of_week = [...WEEKDAY_CODES];
    next.day_of_week = "mon";
  } else if (pattern === "weekly") {
    next.interval_unit = "week";
    next.interval_value = cfg.interval_value || 1;
    if (!next.days_of_week?.length) {
      next.days_of_week = ["mon"];
      next.day_of_week = "mon";
    }
  } else if (pattern === "monthly") {
    next.interval_unit = "month";
    next.interval_value = 1;
  }

  return {
    ...value,
    recurring: true,
    schedule_type:
      pattern === "every_n_minutes" ? "interval" : value.schedule_type,
    schedule_config: next,
  };
};

export const applyIntervalUnit = (
  value: ScheduleValue,
  unit: IntervalUnit,
  intervalValue?: number,
): ScheduleValue => {
  const nextValue = intervalValue ?? value.schedule_config.interval_value ?? 1;
  return {
    ...value,
    recurring: true,
    schedule_type:
      unit === "minute" || unit === "hour" ? "interval" : value.schedule_type,
    time_frequency: intervalValueToMinutes(unit, nextValue),
    schedule_config: {
      ...value.schedule_config,
      recurrence_pattern: "every_n_minutes",
      interval_unit: unit,
      interval_value: nextValue,
    },
  };
};

export const toApiSchedule = (value: ScheduleValue) => {
  if (!value.recurring || value.schedule_type === "manual") {
    return {
      schedule_type: "manual" as const,
      time_frequency: String(value.time_frequency || "15"),
      schedule_config: {},
    };
  }

  const cfg = value.schedule_config;
  const pattern = cfg.recurrence_pattern;
  const [h, m] = (cfg.time || "09:00").split(":");
  const hour = Number(h);
  const minute = Number(m);
  const timezone = cfg.timezone || "UTC";

  if (pattern === "every_n_minutes") {
    const unit = cfg.interval_unit === "hour" ? "hour" : "minute";
    const n = Math.max(1, Number(cfg.interval_value) || 15);
    const minutes = intervalValueToMinutes(unit, n);
    return {
      time_frequency: String(minutes),
      schedule_type: "interval" as const,
      schedule_config: {
        recurrence_pattern: pattern,
        interval_unit: unit,
        interval_value: n,
      },
    };
  }

  const cronCfg: Record<string, string | number | string[]> = {
    recurrence_pattern: pattern,
    hour: Number.isFinite(hour) ? hour : 9,
    minute: Number.isFinite(minute) ? minute : 0,
    timezone,
  };

  if (pattern === "daily") {
    cronCfg.cron_type = "daily";
  } else if (pattern === "weekdays") {
    cronCfg.cron_type = "weekdays";
  } else if (pattern === "weekly") {
    const weeks = Math.max(1, Number(cfg.interval_value) || 1);
    const days = cfg.days_of_week?.length
      ? cfg.days_of_week
      : [cfg.day_of_week || "mon"];
    if (weeks > 1) {
      return {
        time_frequency: String(intervalValueToMinutes("week", weeks)),
        schedule_type: "interval" as const,
        schedule_config: {
          recurrence_pattern: pattern,
          interval_unit: "week",
          interval_value: weeks,
          days_of_week: days,
          day_of_week: days[0],
          hour: cronCfg.hour,
          minute: cronCfg.minute,
          timezone,
        },
      };
    }
    if (isWeekdaysSet(days)) {
      cronCfg.cron_type = "weekdays";
    } else {
      cronCfg.cron_type = "weekly";
      cronCfg.days_of_week = days;
      cronCfg.day_of_week = days[0];
    }
  } else if (pattern === "monthly") {
    cronCfg.cron_type = "monthly";
    if (cfg.monthly_mode === "relative") {
      const monthlyDayCron = monthlyRelativeCronDay(
        cfg.monthly_week,
        cfg.monthly_weekday,
      );
      cronCfg.monthly_mode = "relative";
      cronCfg.monthly_week = cfg.monthly_week;
      cronCfg.monthly_weekday = cfg.monthly_weekday;
      cronCfg.monthly_day_cron = monthlyDayCron;
      cronCfg.day = monthlyDayCron;
    } else {
      cronCfg.monthly_mode = "day_of_month";
      const day = Number(cfg.day);
      cronCfg.day = Number.isFinite(day) ? Math.min(Math.max(day, 1), 31) : 1;
    }
  }

  return {
    time_frequency: "1440",
    schedule_type: "cron" as const,
    schedule_config: cronCfg,
  };
};

export const formatRecurrenceSummary = (value: ScheduleValue): string => {
  if (!value.recurring || value.schedule_type === "manual") {
    return "Manual — run pipeline on demand only.";
  }

  const cfg = value.schedule_config;
  const pattern = cfg.recurrence_pattern;
  const time = cfg.time || "09:00";
  const tz = cfg.timezone ? ` ${cfg.timezone}` : "";
  const scheduleTz = cfg.timezone || getBrowserTimezone();
  const effective = formatShortDate(value.sync_start_date, scheduleTz);
  const until =
    value.end_mode === "on_date" && value.sync_end_date
      ? formatShortDate(value.sync_end_date, scheduleTz)
      : null;

  let occurrence = "";
  if (pattern === "every_n_minutes") {
    const unit = cfg.interval_unit === "hour" ? "hour" : "minute";
    const n = Math.max(1, Number(cfg.interval_value) || 15);
    if (unit === "hour") {
      occurrence = n === 1 ? "every hour" : `every ${n} hours`;
    } else {
      occurrence = n === 1 ? "every minute" : `every ${n} minutes`;
    }
  } else if (pattern === "daily") {
    occurrence = `daily at ${time}${tz}`;
  } else if (pattern === "weekdays") {
    occurrence = `every weekday at ${time}${tz}`;
  } else if (pattern === "weekly") {
    const weeks = Math.max(1, Number(cfg.interval_value) || 1);
    const days = cfg.days_of_week?.length
      ? cfg.days_of_week.map(dayCodeLabel).join(", ")
      : dayCodeLabel(cfg.day_of_week);
    occurrence =
      weeks > 1
        ? `every ${weeks} weeks on ${days} at ${time}${tz}`
        : `every ${days} at ${time}${tz}`;
  } else if (pattern === "monthly") {
    if (cfg.monthly_mode === "relative") {
      occurrence = `on the ${cfg.monthly_week} ${dayCodeLabel(cfg.monthly_weekday)} of every month at ${time}${tz}`;
    } else {
      occurrence = `monthly on day ${cfg.day} at ${time}${tz}`;
    }
  } else {
    occurrence = frequencyLabel(value.time_frequency);
  }

  let sentence = `Occurs ${occurrence}`;
  if (effective) {
    sentence += ` effective ${effective}`;
  }
  if (until) {
    sentence += ` until ${until}`;
  }
  return `${sentence}.`;
};

export const batchScheduleLabel = (batch: {
  time_frequency?: string | number;
  schedule_type?: string;
  schedule_config?: BatchScheduleConfig | null;
  sync_end_date?: string | null;
}): string => {
  if (batch.schedule_type === "manual") {
    return "Manual only";
  }
  if (batch.schedule_type !== "cron") {
    const cfg = (batch.schedule_config ?? {}) as Record<string, unknown>;
    if (cfg.interval_unit === "week") {
      const weeks = Number(cfg.interval_value ?? 1);
      return weeks === 1 ? "Every week" : `Every ${weeks} weeks`;
    }
    return frequencyLabel(batch.time_frequency ?? 15);
  }
  const cfg = batch.schedule_config ?? {};
  const cronType = String(cfg.cron_type ?? "weekdays");
  const hour = Number(cfg.hour ?? 0);
  const minute = Number(cfg.minute ?? 0);
  const hh = String(Number.isFinite(hour) ? hour : 0).padStart(2, "0");
  const mm = String(Number.isFinite(minute) ? minute : 0).padStart(2, "0");
  const at = `${hh}:${mm}`;
  const tz = cfg.timezone ? ` ${cfg.timezone}` : "";
  if (cronType === "weekly") {
    const days = Array.isArray(cfg.days_of_week)
      ? (cfg.days_of_week as string[]).join(", ")
      : String(cfg.day_of_week ?? "mon");
    return `Weekly (${days}) ${at}${tz}`;
  }
  if (cronType === "monthly") {
    const monthlyMode = String(cfg.monthly_mode ?? "");
    if (
      monthlyMode === "relative" ||
      typeof cfg.monthly_day_cron === "string"
    ) {
      const dayCron = String(cfg.monthly_day_cron ?? cfg.day ?? "1st fri");
      return `Monthly (${dayCron}) ${at}${tz}`;
    }
    const day = Number(cfg.day ?? 1);
    return `Monthly (day ${day}) ${at}${tz}`;
  }
  if (cronType === "daily") {
    return `Daily ${at}${tz}`;
  }
  return `Weekdays ${at}${tz}`;
};

export const pipelineScheduleLabel = (pipeline: {
  readable_schedule?: string;
  time_frequency?: string | number;
  schedule_type?: string;
  schedule_config?: BatchScheduleConfig | null;
  sync_end_date?: string | null;
  schedule_paused?: boolean;
}): string => {
  if (pipeline.schedule_paused) {
    return "Paused";
  }
  if (pipeline.readable_schedule) return pipeline.readable_schedule;
  return batchScheduleLabel(pipeline);
};

export const formatNextSyncLabel = (
  nextRunAt: string | null | undefined,
  status: string,
  schedulePaused = false,
): string | null => {
  if (status === "paused") return "Paused";
  if (schedulePaused) return "Schedule paused";
  if (!nextRunAt) return null;
  const parsed = new Date(nextRunAt);
  if (Number.isNaN(parsed.getTime())) return null;
  return `Next: ${parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })}`;
};

export const startNodeScheduleLabels = (pipeline: {
  readable_schedule?: string;
  time_frequency?: string | number;
  schedule_type?: string;
  schedule_config?: BatchScheduleConfig | null;
  sync_end_date?: string | null;
  status?: string;
  schedule_paused?: boolean;
  next_run_at?: string | null;
}): { scheduleLabel: string | null; nextSyncLabel: string | null } => {
  const scheduleLabel = pipelineScheduleLabel(pipeline) || null;
  const nextSyncLabel =
    pipeline.schedule_type === "manual"
      ? "Manual"
      : formatNextSyncLabel(
          pipeline.next_run_at,
          pipeline.status ?? "active",
          Boolean(pipeline.schedule_paused),
        );
  return { scheduleLabel, nextSyncLabel };
};

export const splitSyncStartDate = (
  syncStartDate: string | null,
  fallbackTime: string,
): { date: string; time: string } => {
  if (!syncStartDate) {
    return { date: "", time: fallbackTime };
  }
  const parsed = new Date(syncStartDate);
  if (Number.isNaN(parsed.getTime())) {
    if (syncStartDate.includes("T")) {
      const [date, timePart] = syncStartDate.split("T");
      return { date, time: (timePart ?? fallbackTime).slice(0, 5) };
    }
    return { date: syncStartDate.slice(0, 10), time: fallbackTime };
  }
  const date = [
    parsed.getFullYear(),
    String(parsed.getMonth() + 1).padStart(2, "0"),
    String(parsed.getDate()).padStart(2, "0"),
  ].join("-");
  const time = [
    String(parsed.getHours()).padStart(2, "0"),
    String(parsed.getMinutes()).padStart(2, "0"),
  ].join(":");
  return { date, time };
};

export const mergeSyncStartDate = (
  date: string,
  time: string,
): string | null => {
  if (!date) return null;
  return `${date}T${time || "09:00"}`;
};

export const weeklySelectionValid = (value: ScheduleValue): boolean => {
  if (value.schedule_config.recurrence_pattern !== "weekly") return true;
  return (value.schedule_config.days_of_week?.length ?? 0) > 0;
};
