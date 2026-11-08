/** Normalize connector names for file-source flows (S3 / SFTP / Drive / ADLS). */
export function normalizeFileSourceKey(source?: string | null): string {
  return (source || "").toLowerCase().replace(/[\s\-._]/g, "");
}

export function isFileBasedSource(source?: string | null): boolean {
  const key = normalizeFileSourceKey(source);
  return (
    key === "amazons3" ||
    key === "s3" ||
    key === "sftp" ||
    key === "googledrive" ||
    key === "azuredatalakestorage" ||
    key === "adls"
  );
}

/** Django list/preview/PK URL segment for a file source. */
export function fileSourceApiSegment(source?: string | null): string {
  const key = normalizeFileSourceKey(source);
  if (key === "sftp") return "sftp";
  if (key === "googledrive") return "googledrive";
  if (key === "azuredatalakestorage" || key === "adls") {
    return "azuredatalakestorage";
  }
  return "s3";
}

export type FileSourceUiType = "s3" | "sftp" | "googledrive" | "adls";

export function fileSourceUiType(source?: string | null): FileSourceUiType {
  const key = normalizeFileSourceKey(source);
  if (key === "sftp") return "sftp";
  if (key === "googledrive") return "googledrive";
  if (key === "azuredatalakestorage" || key === "adls") return "adls";
  return "s3";
}
