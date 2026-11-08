import React, {
  startTransition,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Box,
  Button,
  Checkbox,
  Dialog,
  Field,
  Flex,
  Input,
  Portal,
  Select,
  Text,
  Textarea,
  VStack,
  createListCollection,
} from "@chakra-ui/react";

import { IoMdArrowBack } from "react-icons/io";
import { MdDelete, MdOutlineSave } from "react-icons/md";

import MultipleMapping from "@/components/dashboard/components/Connectors/components/NewConnector/components/SingleMapping/MultipleMapping";
import SingleMapping, {
  Mapping,
} from "@/components/dashboard/components/Connectors/components/NewConnector/components/SingleMapping/SingleMapping";
import { PasswordInput } from "@/components/ui/password-input";
import { toaster } from "@/components/ui/toaster";

import GoogleDriveOAuth, { GoogleDriveTokens } from "./GoogleDriveOAuth";

export interface S3FieldSchema {
  name: string;
  label: string;
  type: "CharField" | "ChoiceField" | "TextField" | "IntegerField";
  required: boolean;
  widget?: "PasswordInput" | "Checkbox" | null;
  is_visible?: boolean;
  choices?: Array<{
    value: string;
    display: string;
    description?: string;
  }>;
  depend_on?: string | null;
  dependency_value?: string | null;
  description?: string;
  placeholder?: string;
  default_value?: string;
  read_only?: boolean;
}

interface S3DynamicFormProps {
  schema: S3FieldSchema[];
  onSubmit: (_values: Record<string, unknown>) => void;
  loading?: boolean;
  defaultValues?: Record<string, unknown>;
  handleBackButtonClick?: () => void;
  mode?: "create" | "edit";
  destinationName?: string;
  sourceName?: string;
  isSftp?: boolean;
  hideSubmitButton?: boolean;
  leftButtons?: React.ReactNode;
  rightButtons?: React.ReactNode;
  onValuesChange?: (_values: Record<string, unknown>) => void;
  connectionId?: number;
  migrationStatus?: string;
}

// Fields that should always be hidden - these will NEVER be shown
const HIDDEN_FIELDS = [
  "custom_primary",
  "custom_primary_key",
  "single_file_table_mapping",
  "table_to_files_mapping",
  "multi_files_table_name",
  "multi_files_prefix",
];

const SCHEMA_VALIDATION_MESSAGE =
  "Invalid schema name. Schema name shouldn't be empty, should contain only letters, numbers, or underscores, and cannot begin with a number.";

const validateSchemaName = (value: string): string => {
  const trimmed = value.trim();
  const schemaRegex = /^[A-Za-z_][A-Za-z0-9_]*$/;
  return schemaRegex.test(trimmed) ? "" : SCHEMA_VALIDATION_MESSAGE;
};

const S3DynamicForm: React.FC<S3DynamicFormProps> = ({
  schema,
  onSubmit,
  defaultValues,
  loading,
  handleBackButtonClick,
  mode,
  destinationName: _destinationName,
  sourceName: _sourceName,
  isSftp,
  hideSubmitButton = false,
  leftButtons,
  rightButtons,
  onValuesChange,
  connectionId,
  migrationStatus,
}) => {
  const normalizedSourceName = (_sourceName || "")
    .toLowerCase()
    .replace(/[\s\-._]/g, "");

  const isGoogleDriveCreate =
    normalizedSourceName === "googledrive" && mode === "create";

  // Initialize form values from schema and defaultValues
  const initialValues = useMemo(() => {
    const initial = schema.reduce(
      (acc, field) => {
        const val = defaultValues?.[field.name];
        let finalizedValue = "";

        if (val !== undefined && val !== null) {
          finalizedValue =
            typeof val === "object" ? JSON.stringify(val) : String(val);
        } else {
          finalizedValue = field.default_value ?? "";
        }

        return {
          ...acc,
          [field.name]: finalizedValue,
        };
      },
      {} as Record<string, string>,
    );

    // Also include hidden fields from defaultValues
    HIDDEN_FIELDS.forEach((fieldName) => {
      const val = defaultValues?.[fieldName];
      if (val !== undefined && val !== null) {
        initial[fieldName] =
          typeof val === "object" ? JSON.stringify(val) : String(val);
      }
    });

    return initial;
  }, [schema, defaultValues]);

  const [values, setValues] = useState<Record<string, string>>(
    () => initialValues,
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isMappingModalOpen, setIsMappingModalOpen] = useState(false);
  const [lastMappingSavedAt, setLastMappingSavedAt] = useState<number>(0);

  // ── Google Drive OAuth state ──────────────────────────────────────────────
  const [isGoogleAuthorized, setIsGoogleAuthorized] = useState(false);

  // Derived: whether both credential fields are filled (enables Authorize button).
  // Walk the schema to find the actual field names — they may differ across environments.
  const canAuthorize = useMemo(() => {
    const clientIdField = schema.find((f) => {
      const n = f.name.toLowerCase();
      const l = (f.label ?? "").toLowerCase();
      return (
        n.includes("client_id") ||
        n.includes("clientid") ||
        l.includes("client id")
      );
    });
    const clientSecretField = schema.find((f) => {
      const n = f.name.toLowerCase();
      const l = (f.label ?? "").toLowerCase();
      return (
        n.includes("client_secret") ||
        n.includes("clientsecret") ||
        l.includes("client secret")
      );
    });
    const idFilled = !!(clientIdField && values[clientIdField.name]?.trim());
    const secretFilled = !!(
      clientSecretField && values[clientSecretField.name]?.trim()
    );
    return idFilled && secretFilled;
  }, [schema, values]);

  // Resolved actual values for the credential fields (passed explicitly to GoogleDriveOAuth)
  const resolvedClientId = useMemo(() => {
    const f = schema.find((field) => {
      const n = field.name.toLowerCase();
      const l = (field.label ?? "").toLowerCase();
      return (
        n.includes("client_id") ||
        n.includes("clientid") ||
        l.includes("client id")
      );
    });
    return f ? (values[f.name] ?? "") : "";
  }, [schema, values]);

  const resolvedClientSecret = useMemo(() => {
    const f = schema.find((field) => {
      const n = field.name.toLowerCase();
      const l = (field.label ?? "").toLowerCase();
      return (
        n.includes("client_secret") ||
        n.includes("clientsecret") ||
        l.includes("client secret")
      );
    });
    return f ? (values[f.name] ?? "") : "";
  }, [schema, values]);

  const valuesRef = useRef(values);
  const mappingOverridesRef = useRef<Record<string, string>>({});
  const defaultValuesSerializedRef = useRef<string | null>(
    defaultValues ? JSON.stringify(defaultValues) : null,
  );
  const isDirtyRef = useRef(false);

  useEffect(() => {
    valuesRef.current = values;
    onValuesChange?.(values);
  }, [values, onValuesChange]);

  useEffect(() => {
    if (!defaultValues) return;

    const incoming = JSON.stringify(defaultValues);
    if (incoming === defaultValuesSerializedRef.current) return;
    defaultValuesSerializedRef.current = incoming;

    if (isDirtyRef.current) return;

    startTransition(() => {
      setValues((prev) => {
        const updated = { ...prev };
        schema.forEach((field) => {
          const val = defaultValues[field.name];
          // Priority: defaultValues > existing value > default_value from schema
          if (val !== undefined && val !== null) {
            updated[field.name] =
              typeof val === "object" ? JSON.stringify(val) : String(val);
          } else if (updated[field.name] === undefined) {
            updated[field.name] = field.default_value ?? "";
          }
        });
        HIDDEN_FIELDS.forEach((fieldName) => {
          const val = defaultValues[fieldName];
          if (val !== undefined && val !== null) {
            updated[fieldName] =
              typeof val === "object" ? JSON.stringify(val) : String(val);
          }
        });
        return updated;
      });
    });
  }, [defaultValues, schema]);

  const handleChange = (
    e: React.ChangeEvent<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >,
  ) => {
    const { name, value } = e.target;

    // Check if field is read-only
    const fieldSchema = schema.find((f) => f.name === name);
    if (mode === "edit" && fieldSchema?.read_only === true) {
      console.warn(`Attempted to change read-only field: ${name}`);
      return;
    }

    // Mark the form as dirty so the defaultValues effect doesn't overwrite
    // the user's in-progress edits during a background refetch.
    isDirtyRef.current = true;

    const currentVals = valuesRef.current;

    // Check if a mapping actually has data (ignore empty stringified JSON from backend)
    const hasAnyMapping = (vals: Record<string, string>) => {
      const isPresent = (v?: string) => v && v !== "[]" && v !== "{}";
      return (
        isPresent(vals.single_file_table_mapping) ||
        isPresent(vals.table_to_files_mapping)
      );
    };

    const mappingsExist = hasAnyMapping(currentVals);

    let clearedReason: string | null = null;

    const isConfigurationField =
      name === "base_folder_path" ||
      name === "include_subfolders" ||
      name === "file_type" ||
      name === "s3_bucket" ||
      name === "sftp_host" ||
      name === "sftp_port" ||
      name === "sftp_username" ||
      name === "sftp_password" ||
      name === "sftp_private_key" ||
      name === "sftp_passphrase" ||
      name === "root_folder";

    if (isConfigurationField && value !== currentVals[name] && mappingsExist) {
      clearedReason = name;
    }

    setValues((prev) => {
      let newValues = { ...prev, [name]: value };
      if (clearedReason) {
        newValues = {
          ...newValues,
          mapping_config: "",
          mapping_id: "",
          mappings: "",
          single_file_table_mapping: "",
          table_to_files_mapping: "",
          multi_files_table_name: "",
          multi_files_prefix: "",
        };
      }

      valuesRef.current = newValues;
      return newValues;
    });
    if (clearedReason) {
      const fieldLabel =
        schema.find((f) => f.name === clearedReason)?.label || clearedReason;
      toaster.warning({
        title: "Mapping cleared",
        description: `${fieldLabel} changed. Please configure the mapping again.`,
      });
    }

    setErrors((prev) => {
      if (name === "destination_schema") {
        return { ...prev, [name]: validateSchemaName(value) };
      }
      return { ...prev, [name]: "" };
    });
  };

  // Filter visible fields based on dependencies and visibility
  const visibleFields = useMemo(() => {
    const isAdls =
      normalizedSourceName === "azuredatalakestorage" ||
      normalizedSourceName === "adls";

    // Dynamically filter fields and update requirement flags
    const dynamicSchema = schema.map((field) => {
      let updatedField = field;

      // ADLS folder name / path / root_folder is optional (files may sit in root container)
      if (isAdls) {
        const nameLower = field.name.toLowerCase();
        const labelLower = (field.label || "").toLowerCase();
        if (
          nameLower.includes("folder") ||
          nameLower === "folder_name" ||
          nameLower === "folder_path" ||
          nameLower === "base_folder_path" ||
          nameLower === "root_folder" ||
          labelLower.includes("folder name") ||
          labelLower.includes("folder path")
        ) {
          updatedField = { ...updatedField, required: false };
        }
      }

      if (updatedField.name === "load_method") {
        const isJsonPacked =
          values.file_type === "json" && values.json_mode === "packed";
        const isXmlPacked =
          values.file_type === "xml" && values.xml_mode === "packed";

        if (isJsonPacked || isXmlPacked) {
          return {
            ...updatedField,
            choices: updatedField.choices?.filter(
              (choice) => choice.value !== "upsert_custom_key",
            ),
          };
        }
      }
      return updatedField;
    });

    const filtered = dynamicSchema.filter((field) => {
      if (HIDDEN_FIELDS.includes(field.name)) return false;

      const hasDependency =
        field.depend_on &&
        field.dependency_value !== null &&
        field.dependency_value !== undefined;

      if (hasDependency && field.depend_on) {
        const dependentValue = String(values[field.depend_on] || "").trim();
        const requiredValue = String(field.dependency_value).trim();
        if (dependentValue !== requiredValue) return false;
      }

      if (field.is_visible === false && !hasDependency) return false;

      return true;
    });

    if (isGoogleDriveCreate) {
      if (!isGoogleAuthorized) {
        // Phase 1: only show the 4 auth fields + the OAuth button (rendered by renderFieldWithGoogleButton)
        return filtered.filter((f) => {
          const n = f.name.toLowerCase();
          const l = f.label?.toLowerCase() || "";
          return (
            n.includes("connection_name") ||
            n.includes("destination_schema") ||
            n.includes("client_id") ||
            l.includes("client id") ||
            n.includes("client_secret") ||
            l.includes("client secret")
          );
        });
      }
      // Phase 2: after OAuth — hide token/credential fields (injected silently),
      // show everything else so user can fill remaining fields
      return filtered.filter((f) => {
        const n = f.name.toLowerCase();
        const isCredential =
          n.includes("access_token") ||
          n.includes("refresh_token") ||
          n.includes("token_expires");
        return !isCredential;
      });
    }

    return filtered;
  }, [schema, values, isGoogleDriveCreate, isGoogleAuthorized]);

  const handleSubmit = () => {
    const latestValues = valuesRef.current;

    const newErrors: Record<string, string> = {};

    visibleFields.forEach((field) => {
      if (field.required && !latestValues[field.name]?.trim()) {
        newErrors[field.name] = `${field.label} is required`;
      }
      if (field.name === "destination_schema") {
        const schemaError = validateSchemaName(latestValues[field.name] || "");
        if (schemaError) {
          newErrors[field.name] = schemaError;
        }
      }
    });

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      toaster.error({
        title: "Validation Error",
        description: "Please fill in all required fields marked with *",
      });
      return;
    }

    // Filter values to only include visible fields and always-included hidden fields
    const visibleFieldNames = new Set(visibleFields.map((f) => f.name));
    const filteredValues: Record<string, string> = {};
    const excludedFields: string[] = [];

    Object.keys(latestValues).forEach((key) => {
      const isVisible = visibleFieldNames.has(key);
      const isHiddenField = HIDDEN_FIELDS.includes(key);
      const value = latestValues[key];
      const hasValue =
        value && (typeof value === "string" ? value.trim() !== "" : true);
      const isOAuthToken =
        key === "access_token" ||
        key === "refresh_token" ||
        key === "token_expires_at";

      if (
        (isVisible && hasValue) ||
        isHiddenField ||
        isOAuthToken ||
        key === "include_subfolders"
      ) {
        filteredValues[key] = latestValues[key];
      } else {
        excludedFields.push(key);
      }
    });

    // Convert numeric strings to actual numbers
    const numericFields = [
      "skipheaderlines",
      "skip_footer_lines",
      "tsv_skipheaderlines",
      "tsv_skip_footer_lines",
    ];
    const normalizedValues: Record<string, unknown> = {};

    Object.keys(filteredValues).forEach((key) => {
      const value = filteredValues[key];
      if (numericFields.includes(key) && typeof value === "string") {
        // Convert to number if it's a numeric string
        const numValue = parseInt(value, 10);
        normalizedValues[key] = isNaN(numValue) ? value : numValue;
      } else {
        normalizedValues[key] = value;
      }
    });

    // Ensure the latest saved mapping values are present even if state lags.
    Object.entries(mappingOverridesRef.current).forEach(([key, value]) => {
      normalizedValues[key] = value;
    });

    // Normalize base_folder_path: remove leading slash, add trailing slash if not empty
    if (normalizedValues.base_folder_path) {
      let basePath = String(normalizedValues.base_folder_path).trim();
      if (basePath) {
        if (basePath.startsWith("/")) {
          basePath = basePath.substring(1);
        }
        if (!basePath.endsWith("/")) {
          basePath += "/";
        }
        normalizedValues.base_folder_path = basePath;
      }
    }

    // Reset dirty flag so the next defaultValues update (fresh server data)
    // is allowed to sync into the form after the successful save.
    isDirtyRef.current = false;

    // If a mapping was just saved, ensure the latest mapping is included.
    const millisSinceMappingSave = Date.now() - lastMappingSavedAt;
    if (millisSinceMappingSave >= 0 && millisSinceMappingSave < 300) {
      setTimeout(() => {
        const latest = valuesRef.current;
        const merged = {
          ...normalizedValues,
          single_file_table_mapping:
            mappingOverridesRef.current.single_file_table_mapping ??
            latest.single_file_table_mapping ??
            normalizedValues.single_file_table_mapping,
          table_to_files_mapping:
            mappingOverridesRef.current.table_to_files_mapping ??
            latest.table_to_files_mapping ??
            normalizedValues.table_to_files_mapping,
          multi_files_table_name:
            mappingOverridesRef.current.multi_files_table_name ??
            latest.multi_files_table_name ??
            normalizedValues.multi_files_table_name,
          multi_files_prefix:
            mappingOverridesRef.current.multi_files_prefix ??
            latest.multi_files_prefix ??
            normalizedValues.multi_files_prefix,
        };
        onSubmit(merged as Record<string, unknown>);
      }, 0);
      return;
    }

    onSubmit(normalizedValues as Record<string, unknown>);
  };

  const isReadOnly = (field: S3FieldSchema) => field.read_only === true;

  // Detect which mapping method is selected
  const mappingMethodValue = values.file_mapping_method || "";
  const isSingleTablePerFile = mappingMethodValue === "single_table_per_file";
  const isMultiFilesSingleTable =
    mappingMethodValue === "multi_files_single_table";

  // Open modal when mapping method is selected

  const handleFileMappingSave = (mappings: Mapping[]) => {
    // Convert array of mappings to object format: {"file.csv": "TABLE_NAME"}
    const mappingsObject = mappings.reduce(
      (acc, mapping) => {
        acc[mapping.fileName] = mapping.tableName;
        return acc;
      },
      {} as Record<string, string>,
    );

    // Mark form dirty so a background defaultValues refresh won't overwrite
    // the newly saved mapping before submit.
    isDirtyRef.current = true;

    // Update form values without submitting
    setValues((prev) => {
      const nextValues = {
        ...prev,
        single_file_table_mapping: JSON.stringify(mappingsObject),
      };
      valuesRef.current = nextValues;
      mappingOverridesRef.current = {
        ...mappingOverridesRef.current,
        single_file_table_mapping: nextValues.single_file_table_mapping,
      };
      setLastMappingSavedAt(Date.now());
      return nextValues;
    });

    // Close the modal
    setIsMappingModalOpen(false);
  };

  const handleFileMappingCancel = () => {
    // Close the modal without clearing settings
    setIsMappingModalOpen(false);
  };

  const currentMappings = useMemo(() => {
    const mappingData = values.single_file_table_mapping;
    if (!mappingData || mappingData === "[]" || mappingData === "{}") return [];

    try {
      let parsed: unknown = mappingData;
      if (typeof mappingData === "string") {
        parsed = JSON.parse(mappingData);
      }
      if (!parsed) return [];

      if (Array.isArray(parsed)) {
        return (parsed as Record<string, unknown>[]).map((m) => ({
          ...m,
          fileName: m.fileName as string,
          tableName: m.tableName as string,
          isSelected: (m.isSelected ?? true) as boolean,
        }));
      }

      if (typeof parsed === "object") {
        const entries = Object.entries(parsed as Record<string, string>);
        if (entries.length === 0) return [];

        // Object format comes from the backend — these are already saved mappings
        return entries.map(([fileName, tableName]) => ({
          fileName,
          tableName: tableName as string,
          isSelected: true,
        }));
      }

      return [];
    } catch {
      return [];
    }
  }, [values.single_file_table_mapping]);

  const currentMultipleFiles = useMemo(() => {
    const mappingData = values.table_to_files_mapping;
    if (!mappingData || mappingData === "[]" || mappingData === "{}") return [];

    try {
      let parsed: unknown = mappingData;
      if (typeof mappingData === "string") {
        parsed = JSON.parse(mappingData);
      }
      if (!parsed) return [];

      if (typeof parsed === "object" && !Array.isArray(parsed)) {
        const objectValues = Object.values(parsed as Record<string, string[]>);
        if (objectValues.length === 0) return [];

        const firstTableFiles = objectValues[0];
        if (Array.isArray(firstTableFiles)) {
          return firstTableFiles as string[];
        }
      }

      if (Array.isArray(parsed)) {
        return parsed as string[];
      }

      return [];
    } catch {
      return [];
    }
  }, [values.table_to_files_mapping]);

  const hasMappings = useMemo(() => {
    const hasMultiTableName = !!values.multi_files_table_name?.trim();
    const hasMultiPrefix = !!values.multi_files_prefix?.trim();
    const hasMultiMappingValue =
      !!values.table_to_files_mapping &&
      values.table_to_files_mapping !== "[]" &&
      values.table_to_files_mapping !== "{}";

    return (
      currentMappings.length > 0 ||
      currentMultipleFiles.length > 0 ||
      hasMultiTableName ||
      hasMultiPrefix ||
      hasMultiMappingValue
    );
  }, [
    currentMappings,
    currentMultipleFiles,
    values.multi_files_table_name,
    values.multi_files_prefix,
    values.table_to_files_mapping,
  ]);

  // Called by GoogleDriveOAuth once tokens are exchanged
  const handleGoogleTokens = (tokens: GoogleDriveTokens) => {
    if (!tokens.access_token?.trim()) return;

    let saved: Record<string, string> = {};

    try {
      const raw = sessionStorage.getItem("gdrive_form_values");
      const parsed: unknown = raw ? JSON.parse(raw) : {};

      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        saved = Object.fromEntries(
          Object.entries(parsed).filter(
            ([, value]) => typeof value === "string",
          ),
        ) as Record<string, string>;
      }
    } catch {
      // Continue with the current form and fresh tokens.
    }

    const nextValues: Record<string, string> = {
      ...valuesRef.current,
      ...saved,
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token ?? "",
      token_expires_at: tokens.token_expires_at ?? "",
      folder_id:
        tokens.folder_id ??
        saved.folder_id ??
        valuesRef.current.folder_id ??
        "",
      folder_name:
        tokens.folder_name ??
        saved.folder_name ??
        valuesRef.current.folder_name ??
        "",
    };

    isDirtyRef.current = true;
    valuesRef.current = nextValues;
    setValues(nextValues);
    setIsGoogleAuthorized(true);

    sessionStorage.removeItem("gdrive_form_values");
    sessionStorage.removeItem("gdrive_return_path");
  };

  const handleClearMapping = () => {
    defaultValuesSerializedRef.current = null;
    mappingOverridesRef.current = {};

    // Keep file_mapping_method so the user's chosen method stays selected;
    // only wipe the actual mapping payload.
    setValues((prev) => {
      const nextValues = {
        ...prev,
        mapping_config: "",
        mapping_id: "",
        mappings: "",
        single_file_table_mapping: "",
        table_to_files_mapping: "",
        multi_files_table_name: "",
        multi_files_prefix: "",
      };
      valuesRef.current = nextValues;
      return nextValues;
    });
  };

  const renderField = (field: S3FieldSchema) => {
    const fieldReadOnly = isReadOnly(field);
    const fieldValue = values[field.name] || "";

    if (field.widget === "Checkbox" || field.name === "include_subfolders") {
      const checkboxElement = (
        <Checkbox.Root
          id={field.name}
          checked={fieldValue === "true"}
          onCheckedChange={(e) => {
            const syntheticEvent = {
              target: {
                name: field.name,
                value: e.checked ? "true" : "false",
              },
            } as React.ChangeEvent<HTMLInputElement>;
            handleChange(syntheticEvent);
          }}
          disabled={fieldReadOnly}
          colorPalette="brand"
          size="sm"
        >
          <Checkbox.HiddenInput />
          <Checkbox.Control
            cursor={fieldReadOnly ? "not-allowed" : "pointer"}
          />
          <Checkbox.Label
            fontSize="sm"
            mt="1"
            cursor={fieldReadOnly ? "not-allowed" : "pointer"}
          >
            {field.label}
          </Checkbox.Label>
        </Checkbox.Root>
      );

      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Flex align="center" gap={2}>
            {checkboxElement}
          </Flex>
          {field.description && (
            <Field.HelperText
              className="checkbox"
              fontSize="xs"
              color="gray.600"
            >
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    if (field.type === "ChoiceField") {
      const choiceItems =
        field.choices?.map((choice) => ({
          label: choice.display,
          value: choice.value,
          description: choice.description,
        })) ?? [];

      const collection = createListCollection({ items: choiceItems });

      const handleSelectChange = (e: { value: string[] }) => {
        const newValue = e.value[0] || "";
        const syntheticEvent = {
          target: { name: field.name, value: newValue },
        } as React.ChangeEvent<HTMLSelectElement>;
        handleChange(syntheticEvent);

        // Open modal if this is the mapping method field and a value is selected
        if (field.name === "file_mapping_method" && newValue) {
          setIsMappingModalOpen(true);
        }
      };

      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Select.Root
            key={`${field.name}-${values.file_type}-${values.file_mapping_method}`}
            collection={collection}
            size="sm"
            disabled={fieldReadOnly}
            value={fieldValue ? [fieldValue] : []}
            onValueChange={handleSelectChange}
            bg={fieldReadOnly ? "gray.200 !important" : "white"}
          >
            <Select.HiddenSelect id={field.name} name={field.name} />

            <Select.Control>
              <Select.Trigger
                bg={fieldReadOnly ? "gray.200 !important" : "white"}
                color={fieldReadOnly ? "black !important" : undefined}
                borderColor={fieldReadOnly ? "gray.300 !important" : undefined}
                opacity={fieldReadOnly ? "1 !important" : undefined}
                cursor={fieldReadOnly ? "not-allowed" : undefined}
              >
                <Select.ValueText
                  placeholder={
                    !field.depend_on
                      ? field.placeholder || "Select option"
                      : undefined
                  }
                />
              </Select.Trigger>
              <Select.IndicatorGroup>
                <Select.Indicator />
              </Select.IndicatorGroup>
            </Select.Control>
            <Portal>
              <Select.Positioner>
                <Select.Content>
                  {collection.items.map((item) => {
                    const choice = field.choices?.find(
                      (c) => c.value === item.value,
                    );
                    return (
                      <Select.Item item={item} key={item.value}>
                        <Flex direction="column" align="flex-start" gap={1}>
                          <Text fontWeight="medium">{item.label}</Text>
                          {choice?.description && (
                            <Text fontSize="xs" color="gray.600">
                              {choice.description}
                            </Text>
                          )}
                        </Flex>
                        <Select.ItemIndicator />
                      </Select.Item>
                    );
                  })}
                </Select.Content>
              </Select.Positioner>
            </Portal>
          </Select.Root>
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600">
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}

          {/* Show Configure Mapping button in both create and edit modes */}
          {field.name === "file_mapping_method" && fieldValue && (
            <Flex gap={2} mt={2}>
              {hasMappings && (
                <Button
                  size="xs"
                  variant="ghost"
                  colorPalette="red"
                  onClick={handleClearMapping}
                  disabled={fieldReadOnly}
                >
                  <MdDelete />
                  Clear Mapping
                </Button>
              )}
              {/* Always allow configuring the mapping, even in edit mode */}
              <Button
                size="xs"
                variant="outline"
                onClick={() => setIsMappingModalOpen(true)}
                disabled={false}
              >
                Configure Mapping
              </Button>
            </Flex>
          )}
        </Field.Root>
      );
    }

    if (field.type === "TextField") {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Textarea
            id={field.name}
            name={field.name}
            value={fieldValue}
            onChange={handleChange}
            autoComplete="off"
            placeholder={
              !field.depend_on
                ? field.placeholder || `Enter ${field.label.toLowerCase()}`
                : undefined
            }
            rows={8}
            fontFamily="monospace"
            fontSize="xs"
            resize="vertical"
            readOnly={fieldReadOnly}
            bg={fieldReadOnly ? "gray.200 !important" : undefined}
            color={fieldReadOnly ? "black !important" : undefined}
            borderColor={fieldReadOnly ? "gray.300 !important" : undefined}
            opacity={fieldReadOnly ? "1 !important" : undefined}
            cursor={fieldReadOnly ? "not-allowed" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600">
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    if (field.type === "IntegerField") {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Input
            id={field.name}
            name={field.name}
            type="number"
            size="sm"
            value={fieldValue}
            onChange={handleChange}
            autoComplete="off"
            placeholder={
              !field.depend_on
                ? field.placeholder || `Enter ${field.label.toLowerCase()}`
                : undefined
            }
            readOnly={fieldReadOnly}
            bg={fieldReadOnly ? "gray.200 !important" : undefined}
            color={fieldReadOnly ? "black !important" : undefined}
            borderColor={fieldReadOnly ? "gray.300 !important" : undefined}
            opacity={fieldReadOnly ? "1 !important" : undefined}
            cursor={fieldReadOnly ? "not-allowed" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600">
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    if (
      (field.type as string) === "PasswordInput" ||
      (field.type as string) === "PasswordField" ||
      field.widget === "PasswordInput"
    ) {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <PasswordInput
            id={field.name}
            name={field.name}
            size="sm"
            value={fieldValue}
            onChange={handleChange}
            autoComplete="new-password"
            placeholder={
              !field.depend_on
                ? field.placeholder || `Enter ${field.label.toLowerCase()}`
                : undefined
            }
            readOnly={fieldReadOnly}
            bg={fieldReadOnly ? "gray.200 !important" : undefined}
            color={fieldReadOnly ? "black !important" : undefined}
            borderColor={fieldReadOnly ? "gray.300 !important" : undefined}
            opacity={fieldReadOnly ? "1 !important" : undefined}
            cursor={fieldReadOnly ? "not-allowed" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600">
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    return (
      <Field.Root
        key={field.name}
        required={field.required}
        invalid={!!errors[field.name]}
      >
        <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
        <Input
          id={field.name}
          name={field.name}
          type="text"
          size="sm"
          value={fieldValue}
          onChange={handleChange}
          autoComplete="off"
          placeholder={
            !field.depend_on
              ? field.placeholder || `Enter ${field.label.toLowerCase()}`
              : undefined
          }
          readOnly={fieldReadOnly}
          bg={fieldReadOnly ? "gray.200 !important" : undefined}
          color={fieldReadOnly ? "black !important" : undefined}
          borderColor={fieldReadOnly ? "gray.300 !important" : undefined}
          opacity={fieldReadOnly ? "1 !important" : undefined}
          cursor={fieldReadOnly ? "not-allowed" : undefined}
        />
        {field.description && (
          <Field.HelperText fontSize="xs" color="gray.600">
            {field.description}
          </Field.HelperText>
        )}
        {errors[field.name] && (
          <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
        )}
      </Field.Root>
    );
  };

  // ---------- NEW: Render fields with dependent fields inside a box ----------
  const renderedParents = new Set<string>();

  const renderFieldWithGoogleButton = (field: S3FieldSchema) => {
    const baseElement = renderField(field);
    const isClientSecret =
      field.name === "client_secret" ||
      field.label?.toLowerCase() === "client secret" ||
      field.name.toLowerCase().includes("client_secret");

    if (normalizedSourceName === "googledrive" && isClientSecret) {
      return (
        <Box key={`google-wrap-${field.name}`}>
          {baseElement}
          <GoogleDriveOAuth
            formValues={values}
            clientId={resolvedClientId}
            clientSecret={resolvedClientSecret}
            canAuthorize={canAuthorize}
            onTokensReceived={handleGoogleTokens}
          />
        </Box>
      );
    }
    return baseElement;
  };

  const renderFieldsWithDependencies = () => {
    return visibleFields.map((field) => {
      if (field.depend_on && renderedParents.has(field.depend_on)) return null;

      const parentFieldElement = renderFieldWithGoogleButton(field);

      const dependentFields = visibleFields.filter(
        (f) =>
          f.depend_on === field.name &&
          f.dependency_value?.toLowerCase() ===
            (values[field.name] || "").toLowerCase(),
      );

      renderedParents.add(field.name);

      if (dependentFields.length === 0) return parentFieldElement;

      return (
        <Box key={`parent-${field.name}`}>
          {parentFieldElement}
          <Box
            border="1px solid"
            borderColor="gray.200"
            p={3}
            borderRadius="md"
            mt={2}
            bg="white"
          >
            <VStack gap={3} align="stretch">
              {dependentFields.map((dep) => renderFieldWithGoogleButton(dep))}
            </VStack>
          </Box>
        </Box>
      );
    });
  };

  // Apply file_mapping_method read_only only where needed (MultipleMapping fields).
  const isMappingReadOnly =
    !!schema.find((f) => f.name === "file_mapping_method")?.read_only ||
    migrationStatus === "C";

  return (
    <>
      <form
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
        style={{ display: "contents" }}
      >
        <VStack
          gap={3}
          align="stretch"
          as="div"
          maxW="lg"
          opacity={isMappingModalOpen ? 0.5 : 1}
          pointerEvents={isMappingModalOpen ? "none" : "auto"}
          transition="opacity 0.2s"
        >
          {renderFieldsWithDependencies()}
          <Flex justifyContent="space-between">
            <Flex gap={4}>
              {handleBackButtonClick && (
                <Button variant="outline" onClick={handleBackButtonClick}>
                  <IoMdArrowBack />
                  Back
                </Button>
              )}
              {leftButtons}
            </Flex>
            <Flex gap={4}>
              {rightButtons}
              {!hideSubmitButton &&
                !(isGoogleDriveCreate && !isGoogleAuthorized) && (
                  <Button
                    type="button"
                    colorPalette="brand"
                    onClick={handleSubmit}
                    loading={loading}
                    disabled={loading}
                  >
                    <MdOutlineSave />
                    {mode === "create" ? "Create" : "Save"}
                  </Button>
                )}
            </Flex>
          </Flex>
        </VStack>
      </form>

      {isMappingModalOpen && (
        <Dialog.Root open={isMappingModalOpen} closeOnInteractOutside={false}>
          <Portal>
            <Dialog.Backdrop />
            <Dialog.Positioner>
              <Dialog.Content
                maxW="1100px"
                w="90vw"
                maxH="90vh"
                overflow="hidden"
              >
                {isSingleTablePerFile ? (
                  <SingleMapping
                    formValues={values}
                    mappings={currentMappings}
                    connectionId={connectionId}
                    onCancel={handleFileMappingCancel}
                    onSaveMappings={handleFileMappingSave}
                    loading={loading}
                    readOnly={false}
                    isSftp={isSftp || _sourceName?.toLowerCase() === "sftp"}
                    sourceType={normalizedSourceName}
                  />
                ) : isMultiFilesSingleTable ? (
                  <MultipleMapping
                    formValues={values}
                    tableName={values.multi_files_table_name || ""}
                    selectedFiles={currentMultipleFiles}
                    connectionId={connectionId}
                    isSftp={isSftp || _sourceName?.toLowerCase() === "sftp"}
                    sourceType={normalizedSourceName}
                    onSave={(data) => {
                      // Format table_to_files_mapping as object: { "TABLE_NAME": ["file1", "file2"] }
                      const tableToFilesMapping = {
                        [data.tableName]: data.selectedFiles,
                      };

                      // Mark form dirty so a background defaultValues refresh won't overwrite
                      // the newly saved mapping before submit.
                      isDirtyRef.current = true;

                      // Update form values without submitting
                      setValues((prev) => {
                        const nextValues = {
                          ...prev,
                          multi_files_table_name: data.tableName,
                          multi_files_prefix: data.prefix,
                          table_to_files_mapping:
                            JSON.stringify(tableToFilesMapping),
                        };
                        valuesRef.current = nextValues;
                        mappingOverridesRef.current = {
                          ...mappingOverridesRef.current,
                          multi_files_table_name:
                            nextValues.multi_files_table_name,
                          multi_files_prefix: nextValues.multi_files_prefix,
                          table_to_files_mapping:
                            nextValues.table_to_files_mapping,
                        };
                        setLastMappingSavedAt(Date.now());
                        return nextValues;
                      });

                      // Close the modal
                      setIsMappingModalOpen(false);
                    }}
                    onCancel={handleFileMappingCancel}
                    loading={loading}
                    readOnly={isMappingReadOnly}
                  />
                ) : null}
              </Dialog.Content>
            </Dialog.Positioner>
          </Portal>
        </Dialog.Root>
      )}
    </>
  );
};

export default S3DynamicForm;
