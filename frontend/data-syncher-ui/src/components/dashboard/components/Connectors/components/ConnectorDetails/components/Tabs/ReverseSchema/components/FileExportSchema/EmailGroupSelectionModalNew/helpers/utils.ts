export const getPathLabel = (
  dest?: string,
  customPathLabel?: string | null,
): string => {
  if (customPathLabel) return customPathLabel;
  const normalized = dest?.toLowerCase() || "destination";
  if (normalized === "sharepoint") return "SharePoint Path";
  if (normalized === "s3") return "S3 Path";
  if (normalized === "googledrive") return "Google Drive Path";
  return "Destination Path";
};

export const getDestinationTitle = (dest: string): string => {
  if (!dest) return "SharePoint";
  return dest.charAt(0).toUpperCase() + dest.slice(1).toLowerCase();
};

export const getFileExtension = (format: string | null | undefined): string => {
  const norm = format?.toLowerCase() || "";
  if (norm === "excel") return ".xlsx";
  if (norm === "parquet") return ".parquet";
  if (norm === "json") return ".json";
  return ".csv";
};

export const buildPreviewPath = (params: {
  rootFolder?: string | null;
  targetFolder?: string | null;
  outputFileName?: string | null;
  fileFormat?: string | null;
  tableName: string;
}): string => {
  const { rootFolder, targetFolder, outputFileName, fileFormat, tableName } =
    params;
  let fileName = (outputFileName || "").trim();
  if (!fileName) {
    const ext = getFileExtension(fileFormat);
    fileName = tableName ? `${tableName}${ext}` : "";
  } else {
    const hasExt = /\.(csv|xlsx|xls|json|parquet)$/i.test(fileName);
    if (!hasExt) {
      const ext = getFileExtension(fileFormat);
      fileName = `${fileName}${ext}`;
    }
  }

  const parts = [rootFolder, targetFolder, fileName].map((p) =>
    (p || "").trim(),
  );
  const pathParts = parts
    .filter(Boolean)
    .map((p) => p.replace(/^\/+|\/+$/g, ""))
    .filter(Boolean);

  let fullPath = pathParts.join("/");

  const startsWithSlash = rootFolder && rootFolder.trim().startsWith("/");
  if (startsWithSlash && !fullPath.startsWith("/")) {
    fullPath = "/" + fullPath;
  }

  return fullPath || (tableName ? `${tableName}.xlsx` : "");
};

export const renderTemplateText = (
  template: string,
  tableName: string,
  destinationName?: string,
  resolvedPath?: string,
  connectionName?: string,
  companyName?: string,
  sourceDisplayName?: string,
  columnsCount?: number,
): string => {
  if (!template) return "";
  const destTitle = getDestinationTitle(destinationName || "");
  const conn =
    connectionName ||
    (destinationName ? `${destinationName} Connection` : "Source Connection");
  const comp = companyName || "Syncdatesy";
  const source = sourceDisplayName || "Source Data";

  return template
    .replace(/\{table\}/g, tableName || "Table")
    .replace(/\{destination\}/g, destTitle)
    .replace(/\{file_name\}/g, resolvedPath || `${tableName}.xlsx`)
    .replace(/\{path\}/g, resolvedPath || "")
    .replace(/\{connection\}/g, conn)
    .replace(/\{company\}/g, comp)
    .replace(/\{source\}/g, source)
    .replace(/\{service\}/g, source)
    .replace(/\{columns\}/g, String(columnsCount ?? ""));
};

export const renderSubject = (
  template: string,
  tableName: string,
  destinationName?: string,
  resolvedPath?: string,
  connectionName?: string,
  companyName?: string,
  sourceDisplayName?: string,
): string => {
  return renderTemplateText(
    template,
    tableName,
    destinationName,
    resolvedPath,
    connectionName,
    companyName,
    sourceDisplayName,
  );
};
