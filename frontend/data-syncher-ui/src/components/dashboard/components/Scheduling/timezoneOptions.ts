/** Teams / Outlook-style standard time zones (IANA id + friendly label). */
export type StandardTimezone = {
  iana: string;
  name: string;
};

export const STANDARD_TIMEZONES: StandardTimezone[] = [
  { iana: "UTC", name: "Coordinated Universal Time" },
  { iana: "Pacific/Honolulu", name: "Hawaii" },
  { iana: "America/Anchorage", name: "Alaska" },
  { iana: "America/Los_Angeles", name: "Pacific Time (US & Canada)" },
  { iana: "America/Tijuana", name: "Baja California" },
  { iana: "America/Phoenix", name: "Arizona" },
  { iana: "America/Denver", name: "Mountain Time (US & Canada)" },
  { iana: "America/Chicago", name: "Central Time (US & Canada)" },
  {
    iana: "America/Mexico_City",
    name: "Guadalajara, Mexico City, Monterrey",
  },
  { iana: "America/New_York", name: "Eastern Time (US & Canada)" },
  { iana: "America/Bogota", name: "Bogota, Lima, Quito, Rio Branco" },
  { iana: "America/Halifax", name: "Atlantic Time (Canada)" },
  { iana: "America/Caracas", name: "Caracas" },
  { iana: "America/St_Johns", name: "Newfoundland" },
  { iana: "America/Sao_Paulo", name: "Brasilia" },
  { iana: "America/Argentina/Buenos_Aires", name: "Buenos Aires" },
  { iana: "Atlantic/Azores", name: "Azores" },
  {
    iana: "Europe/London",
    name: "Dublin, Edinburgh, Lisbon, London",
  },
  {
    iana: "Europe/Berlin",
    name: "Amsterdam, Berlin, Bern, Rome, Stockholm, Vienna",
  },
  {
    iana: "Europe/Paris",
    name: "Brussels, Copenhagen, Madrid, Paris",
  },
  { iana: "Africa/Lagos", name: "West Central Africa" },
  { iana: "Europe/Athens", name: "Athens, Bucharest" },
  { iana: "Asia/Jerusalem", name: "Jerusalem" },
  { iana: "Europe/Istanbul", name: "Istanbul" },
  { iana: "Europe/Moscow", name: "Moscow, St. Petersburg" },
  { iana: "Asia/Tehran", name: "Tehran" },
  { iana: "Asia/Dubai", name: "Abu Dhabi, Muscat" },
  { iana: "Asia/Kabul", name: "Kabul" },
  { iana: "Asia/Karachi", name: "Islamabad, Karachi" },
  {
    iana: "Asia/Kolkata",
    name: "Chennai, Kolkata, Mumbai, New Delhi",
  },
  { iana: "Asia/Kathmandu", name: "Kathmandu" },
  { iana: "Asia/Dhaka", name: "Dhaka" },
  { iana: "Asia/Yangon", name: "Yangon (Rangoon)" },
  { iana: "Asia/Bangkok", name: "Bangkok, Hanoi, Jakarta" },
  {
    iana: "Asia/Shanghai",
    name: "Beijing, Chongqing, Hong Kong, Urumqi",
  },
  { iana: "Asia/Singapore", name: "Kuala Lumpur, Singapore" },
  { iana: "Australia/Perth", name: "Perth" },
  { iana: "Asia/Tokyo", name: "Osaka, Sapporo, Tokyo" },
  { iana: "Asia/Seoul", name: "Seoul" },
  { iana: "Australia/Adelaide", name: "Adelaide" },
  { iana: "Australia/Darwin", name: "Darwin" },
  {
    iana: "Australia/Sydney",
    name: "Canberra, Melbourne, Sydney",
  },
  { iana: "Australia/Brisbane", name: "Brisbane" },
  { iana: "Pacific/Guadalcanal", name: "Solomon Is., New Caledonia" },
  { iana: "Pacific/Auckland", name: "Auckland, Wellington" },
  { iana: "Pacific/Fiji", name: "Fiji" },
];

const STANDARD_IANA = new Set(STANDARD_TIMEZONES.map((tz) => tz.iana));

/** Winter reference date for stable UTC offset labels (Teams-style). */
const OFFSET_REFERENCE = new Date(Date.UTC(2026, 0, 15, 12, 0, 0));

export const utcOffsetLabel = (timezone: string): string => {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      timeZoneName: "longOffset",
    }).formatToParts(OFFSET_REFERENCE);
    const raw = parts.find((part) => part.type === "timeZoneName")?.value ?? "";
    const match = raw.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/i);
    if (!match) return "(UTC)";
    const sign = match[1];
    const hours = match[2].padStart(2, "0");
    const minutes = (match[3] ?? "00").padStart(2, "0");
    return `(UTC${sign}${hours}:${minutes})`;
  } catch {
    return "(UTC)";
  }
};

export const formatTimezoneLabel = (
  iana: string,
  friendlyName?: string,
): string => {
  const name =
    friendlyName ??
    STANDARD_TIMEZONES.find((tz) => tz.iana === iana)?.name ??
    iana.replace(/_/g, " ");
  return `${utcOffsetLabel(iana)} ${name}`;
};

let cachedAllTimezones: string[] | null = null;

export const listAllTimezones = (): string[] => {
  if (cachedAllTimezones) return cachedAllTimezones;
  const intlWithSupported = Intl as typeof Intl & {
    supportedValuesOf?: (_key: string) => string[];
  };
  if (typeof intlWithSupported.supportedValuesOf === "function") {
    cachedAllTimezones = intlWithSupported.supportedValuesOf("timeZone");
    return cachedAllTimezones;
  }
  cachedAllTimezones = STANDARD_TIMEZONES.map((tz) => tz.iana);
  return cachedAllTimezones;
};

export const isStandardTimezone = (iana: string): boolean =>
  STANDARD_IANA.has(iana);

/** Map deprecated / alias IANA ids to the standard list entry we store in schedules. */
const CANONICAL_TIMEZONE: Record<string, string> = {
  "Asia/Calcutta": "Asia/Kolkata",
};

export const canonicalTimezone = (iana: string): string =>
  CANONICAL_TIMEZONE[iana] ?? iana;

const searchableTimezoneIds = (): string[] => {
  const ids = new Set<string>(STANDARD_TIMEZONES.map((tz) => tz.iana));
  for (const iana of listAllTimezones()) {
    ids.add(iana);
  }
  return [...ids];
};

/** Extra tokens for search (e.g. legacy names like Asia/Calcutta → Asia/Kolkata). */
const TIMEZONE_SEARCH_ALIASES: Record<string, string[]> = {
  "Asia/Kolkata": ["calcutta", "india", "ist"],
  "Asia/Calcutta": ["kolkata", "india", "ist"],
  "America/New_York": ["est", "edt", "eastern"],
  "Europe/London": ["gmt", "bst", "uk"],
  UTC: ["gmt", "zulu"],
};

const timezoneSearchBlob = (iana: string): string => {
  const canonical = canonicalTimezone(iana);
  const standard = STANDARD_TIMEZONES.find((tz) => tz.iana === canonical);
  const label = formatTimezoneLabel(canonical, standard?.name);
  const offset = utcOffsetLabel(canonical).toLowerCase();
  const offsetCompact = offset.replace(/[():]/g, "").replace(/\s+/g, "");
  const aliases = [
    ...(TIMEZONE_SEARCH_ALIASES[iana] ?? []),
    ...(TIMEZONE_SEARCH_ALIASES[canonical] ?? []),
  ];
  return [iana, canonical, label, offset, offsetCompact, ...aliases]
    .join(" ")
    .toLowerCase();
};

export const filterTimezonesForSearch = (
  query: string,
  selected?: string,
  limit = 80,
): { iana: string; label: string }[] => {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const seen = new Set<string>();
  const matches: string[] = [];

  const push = (iana: string) => {
    const canonical = canonicalTimezone(iana);
    if (seen.has(canonical)) return;
    seen.add(canonical);
    matches.push(canonical);
  };

  for (const iana of searchableTimezoneIds()) {
    if (timezoneSearchBlob(iana).includes(q)) {
      push(iana);
    }
  }

  if (selected) {
    push(selected);
  }

  return matches.slice(0, limit).map((iana) => ({
    iana,
    label: formatTimezoneLabel(
      iana,
      STANDARD_TIMEZONES.find((tz) => tz.iana === iana)?.name,
    ),
  }));
};
