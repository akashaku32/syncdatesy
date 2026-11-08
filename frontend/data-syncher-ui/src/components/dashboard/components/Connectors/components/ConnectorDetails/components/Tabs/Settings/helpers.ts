export const syncFrequenciesOptions = [
  { value: "None", label: "None" },
  { value: "1", label: "1 minute" },
  { value: "5", label: "5 minutes" },
  { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "180", label: "3 hours" },
  { value: "360", label: "6 hours" },
  { value: "480", label: "8 hours" },
  { value: "720", label: "12 hours" },
  { value: "1440", label: "24 hours" },
];

export const safetyIntervalOptions = [
  { value: "None", label: "None" },
  { value: "1", label: "1 minute" },
  { value: "5", label: "5 minutes" },
  { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "180", label: "3 hours" },
  { value: "360", label: "6 hours" },
  { value: "480", label: "8 hours" },
  { value: "720", label: "12 hours" },
  { value: "1440", label: "24 hours" },
];

export const executionOrderOptions = [
  { value: "parallel", label: "Parallel" },
  { value: "sequential", label: "Sequential" },
];

export function toLocalDateTimeInput(isoUtc?: string | null): string {
  if (!isoUtc) return "";
  const d = new Date(isoUtc); // parsed to a Date object
  const pad = (n: number) => String(n).padStart(2, "0");
  const YYYY = d.getFullYear();
  const MM = pad(d.getMonth() + 1);
  const DD = pad(d.getDate());
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  // include seconds so `datetime-local` can show and edit seconds when step=1
  return `${YYYY}-${MM}-${DD}T${hh}:${mm}:${ss}`; // "2025-08-11T04:34:27"
}

export function fromLocalDateTimeInput(localValue: string): string | null {
  if (!localValue) return null;
  // `new Date(localValue)` treats the string as local time
  const d = new Date(localValue);
  // keep the ISO in the format `YYYY-MM-DDTHH:mm:ssZ` (no milliseconds)
  return d.toISOString();
}
