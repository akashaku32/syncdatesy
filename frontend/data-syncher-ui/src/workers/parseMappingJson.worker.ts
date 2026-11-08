/// <reference lib="webworker" />

export type ParseMappingResult =
  | { ok: true; data: Record<string, unknown> }
  | { ok: false; error: string };

self.onmessage = (event: MessageEvent<string>) => {
  try {
    const data = JSON.parse(event.data) as Record<string, unknown>;
    if (!data.source || !data.target || !Array.isArray(data.field_mapping)) {
      self.postMessage({
        ok: false,
        error: "Missing required keys: source, target, field_mapping",
      } satisfies ParseMappingResult);
      return;
    }
    self.postMessage({ ok: true, data } satisfies ParseMappingResult);
  } catch (err) {
    self.postMessage({
      ok: false,
      error: err instanceof Error ? err.message : "Invalid JSON",
    } satisfies ParseMappingResult);
  }
};
