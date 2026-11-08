import { format } from "date-fns";

import { dateTimeFormat } from "@/constants/common";

/** Avoid crashing the destinations table if the API sends a bad timestamp. */
export function formatDestinationDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value || "—";
  try {
    return format(d, dateTimeFormat);
  } catch {
    return String(value ?? "");
  }
}
