import { type ConnectorTable } from "@/types/connectors";

/** Mirror backend ``normalize_name`` (Salesforce API → warehouse column names). */
export const normalizeFieldName = (name: string): string =>
  name
    .replace(/__c/gi, "_C")
    .replace(/__+/g, "_")
    .replace(/([a-z])([A-Z])/g, "$1_$2")
    .replace(/([A-Za-z])(\d)/g, "$1_$2")
    .replace(/(\d)([A-Za-z])/g, "$1_$2")
    .toUpperCase();

export const fieldsMatchByName = (
  sourceField: string,
  destinationField: string,
): boolean =>
  normalizeFieldName(sourceField) === normalizeFieldName(destinationField);

export const isPrimaryKey = (
  fieldName: string,
  fieldInfo: string | { data_type?: string; is_primary_key?: boolean } | null,
  primaryKeys?: string[],
): boolean => {
  if (primaryKeys && Array.isArray(primaryKeys) && primaryKeys.length > 0) {
    return primaryKeys.includes(fieldName);
  }
  if (
    typeof fieldInfo === "object" &&
    typeof fieldInfo?.is_primary_key === "boolean"
  ) {
    return fieldInfo.is_primary_key;
  }

  const primaryKeyPatterns = [/^id$/, /_id$/, /^pk_/, /primary_key/];
  return primaryKeyPatterns.some((pattern) => pattern.test(fieldName));
};

export const findPrimaryKey = (tableData: ConnectorTable): string | null => {
  for (const [fieldName, fieldInfo] of Object.entries(tableData.table_fields)) {
    if (isPrimaryKey(fieldName, fieldInfo)) {
      return fieldName;
    }
  }
  return null;
};

export const hasMatchingFields = (
  sourceTableData: ConnectorTable,
  destinationTableData: ConnectorTable,
): boolean => {
  const sourceFields = Object.keys(sourceTableData.table_fields);
  const destinationFields = Object.keys(destinationTableData.table_fields);
  const destinationNorm = new Set(
    destinationFields.map((field) => normalizeFieldName(field)),
  );

  return sourceFields.some((sourceField) =>
    destinationNorm.has(normalizeFieldName(sourceField)),
  );
};

export type ValidationResult = {
  isValid: boolean;
  error?: {
    title: string;
    description: string;
  };
};

export const validateTableMapping = (
  sourceTableData: ConnectorTable,
  destinationTableData: ConnectorTable,
  sourceField: string,
  destinationField: string,
): ValidationResult => {
  const sourcePK = findPrimaryKey(sourceTableData);
  const destinationPK = findPrimaryKey(destinationTableData);

  if (!sourcePK || !destinationPK) {
    return {
      isValid: false,
      error: {
        title: "Primary Key Not Found",
        description:
          "Both source and destination tables must have primary keys to create a mapping.",
      },
    };
  }

  if (!fieldsMatchByName(sourcePK, destinationPK)) {
    return {
      isValid: false,
      error: {
        title: "Primary Key Mismatch",
        description: `Primary keys do not match. Source: "${sourcePK}", Destination: "${destinationPK}".`,
      },
    };
  }

  if (!hasMatchingFields(sourceTableData, destinationTableData)) {
    return {
      isValid: false,
      error: {
        title: "No Matching Fields",
        description:
          "Source and destination tables must have at least one matching field name to create a mapping.",
      },
    };
  }

  const sourceFieldInfo = sourceTableData.table_fields[sourceField];
  const destinationFieldInfo =
    destinationTableData.table_fields[destinationField];

  if (!sourceFieldInfo || !destinationFieldInfo) {
    return {
      isValid: false,
      error: {
        title: "Error",
        description: "Could not find field data for mapping.",
      },
    };
  }

  const sourceIsPK = isPrimaryKey(sourceField, sourceFieldInfo);
  const destIsPK = isPrimaryKey(destinationField, destinationFieldInfo);

  if (!sourceIsPK || !destIsPK) {
    return {
      isValid: false,
      error: {
        title: "Primary Key Field Required",
        description:
          "You can only map primary key fields. Please drag the primary key field.",
      },
    };
  }

  return { isValid: true };
};

export const validateTableToTableMapping = (
  sourceTableData: ConnectorTable,
  destinationTableData: ConnectorTable,
): ValidationResult => {
  const sourceFieldCount = Object.keys(
    sourceTableData.table_fields || {},
  ).length;
  const destFieldCount = Object.keys(
    destinationTableData.table_fields || {},
  ).length;

  // Fields are loaded on demand for reverse ETL; skip client-side field checks.
  if (sourceFieldCount === 0 || destFieldCount === 0) {
    return { isValid: true };
  }

  // Do not require matching primary keys — PKs often differ by system
  // (e.g. Salesforce "Id" vs Snowflake "ACCOUNT_ID"). Allow mapping when
  // the tables share at least one field name (any field, including PK).
  if (!hasMatchingFields(sourceTableData, destinationTableData)) {
    return {
      isValid: false,
      error: {
        title: "No Matching Fields",
        description:
          "Source and destination tables must have at least one field with the same normalized name (e.g. CREATED_DATE ↔ CreatedDate).",
      },
    };
  }

  return { isValid: true };
};
