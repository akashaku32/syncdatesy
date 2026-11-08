export const MAX_MAPPING_JSON_MB = Number(
  import.meta.env.VITE_MAX_MAPPING_JSON_MB ?? 10,
);
export const MAX_MAPPED_COLUMNS = Number(
  import.meta.env.VITE_MAX_MAPPED_COLUMNS ?? 2000,
);
export const MAX_SCRIPT_KB = Number(import.meta.env.VITE_MAX_SCRIPT_KB ?? 1024);
export const MAX_SCRIPT_LINES = Number(
  import.meta.env.VITE_MAX_SCRIPT_LINES ?? 20000,
);
export const MAX_DRY_RUN_ROWS = Number(
  import.meta.env.VITE_MAX_DRY_RUN_ROWS ?? 1000,
);
export const DEFAULT_DRY_RUN_ROWS = Number(
  import.meta.env.VITE_DEFAULT_DRY_RUN_ROWS ?? 50,
);

export const MAX_MAPPING_JSON_BYTES = MAX_MAPPING_JSON_MB * 1024 * 1024;
export const MAX_SCRIPT_BYTES = MAX_SCRIPT_KB * 1024;

/** Match Django/FastAPI dry-run proxy timeout (300s) with a small buffer. */
export const TRANSFORM_DRY_RUN_TIMEOUT_MS = 330_000;
/** Validate can run AST checks on large scripts; allow more than the default API timeout. */
export const TRANSFORM_VALIDATE_TIMEOUT_MS = 120_000;
