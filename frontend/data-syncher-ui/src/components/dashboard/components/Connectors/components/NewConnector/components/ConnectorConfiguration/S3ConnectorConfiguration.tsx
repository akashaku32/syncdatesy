import { useEffect, useMemo, useState } from "react";

import { Box, Flex, Grid, Text } from "@chakra-ui/react";

import { format } from "date-fns";
import { useNavigate, useParams } from "react-router";

import PrimaryKeySelection from "@/components/dashboard/components/Connectors/components/NewConnector/components/PrimaryKeySelection/PrimaryKeySelection";
import S3DynamicForm, {
  type S3FieldSchema,
} from "@/components/dashboard/helpers/S3DynamicForm";
import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import { dateTimeFormat } from "@/constants/common";
import { VIEW_CONFIG } from "@/constants/view-config";
import useCreateConnection from "@/queryOptions/connector/useCreateConnection";
import useFetchConnectorConfig from "@/queryOptions/connector/useFetchConnectorConfig";
import { useFetchConnectorById } from "@/queryOptions/connector/useFetchConnectorDetailsById";
import useSuggestPrimaryKeys, {
  type SuggestPrimaryKeysRequest,
} from "@/queryOptions/connector/useSuggestPrimaryKeys";
import useUpdateConnectorConfig from "@/queryOptions/connector/useUpdateConnectorConfig";
import useFetchFormSchema from "@/queryOptions/useFetchFormSchema";
import {
  type AuditUser,
  type Connector,
  type CreateConnectionPayload,
} from "@/types/connectors";

import { type ConnectorFormState } from "../../type";
import ConnectorDocsHelperPanel from "./ConnectorDocsHelperPanel";
import S3DocsHelperPanel from "./S3DocsHelperPanel";
import { resolveDestinationSchema } from "./resolveDestinationSchema";
import { useQueryClient } from "@tanstack/react-query";

const getFirstName = (user?: AuditUser | string | null) => {
  if (!user) return "";
  if (typeof user === "string") return user.trim().split(/\s+/)[0] || "";
  return user.first_name || "";
};

const getModifiedByName = (connector?: Connector) =>
  connector
    ? getFirstName(connector.modified_by) ||
      getFirstName(connector.updated_by) ||
      getFirstName(connector.modified_by_name) ||
      getFirstName(connector.updated_by_name) ||
      ""
    : "";

const formatDateTime = (date?: string | number | null) => {
  if (!date || date === "None") return "--";
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return "--";
  return format(parsed, dateTimeFormat);
};

const ModifiedAuditInfo = ({ connector }: { connector?: Connector }) => {
  if (!connector) return null;

  return (
    <Flex gap={4} wrap="wrap">
      <Flex gap={1}>
        <Text fontSize="sm">Modified by:</Text>
        <Text fontSize="sm" fontWeight="semibold">
          {getModifiedByName(connector) || "--"}
        </Text>
      </Flex>
      <Flex gap={1}>
        <Text fontSize="sm">Modified at:</Text>
        <Text fontSize="sm" fontWeight="semibold">
          {formatDateTime(connector.modified_at)}
        </Text>
      </Flex>
    </Flex>
  );
};

const S3ConnectorConfiguration = ({
  state,
  handlePrevious,
  mode = "create",
}: {
  state?: ConnectorFormState;
  handlePrevious?: () => void;
  mode: "create" | "edit";
}) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { connectionId } = useParams<{ connectionId: string }>();
  const shouldFetch = mode === "edit" && !!connectionId;

  // State for primary key selection flow
  const [showPrimaryKeySelection, setShowPrimaryKeySelection] = useState(false);
  const [createdConnectionId, setCreatedConnectionId] = useState<number | null>(
    null,
  );
  const [pendingFormData, setPendingFormData] = useState<{
    connection_name: string;
    destination_schema: string;
    form_data: Record<string, string>;
    destination_config_id?: number;
  } | null>(null);
  // For edit mode: store the last submitted form payload so we can re-submit
  // it with custom_primary_key once the user finishes primary key selection.
  const [editPendingPayload, setEditPendingPayload] = useState<{
    connection_name: string;
    destination_schema: string;
    form_data: Record<string, string>;
  } | null>(null);
  // Tables that still need a custom primary key (from the update API response).
  const [pendingKeyTables, setPendingKeyTables] = useState<string[]>([]);

  // Query hooks
  const { data: connectorData, isPending: isFetchConnectorByIdPending } =
    useFetchConnectorById(shouldFetch ? Number(connectionId) : 0);
  const { data: connectorConfig, isPending: isFetchConnectorConfigPending } =
    useFetchConnectorConfig({
      type: connectorData?.source_name || "",
      id: shouldFetch ? Number(connectionId) : 0,
    });
  const { data: formSchema, isLoading } = useFetchFormSchema({
    type: state?.source || connectorData?.source_name || "",
    source: "source",
  });

  const sourceName = state?.source || connectorData?.source_name || "";
  const normalizedSourceName = sourceName
    ?.toLowerCase()
    .replace(/[\s\-._]/g, "");
  const isSftp = normalizedSourceName === "sftp";
  const isGoogleDrive = normalizedSourceName === "googledrive";
  const isAdls =
    normalizedSourceName === "azuredatalakestorage" ||
    normalizedSourceName === "adls";
  const sourceType = isSftp
    ? "sftp"
    : isGoogleDrive
      ? "googledrive"
      : isAdls
        ? "azuredatalakestorage"
        : "s3";
  const displayName = isSftp
    ? "SFTP"
    : isGoogleDrive
      ? "Google Drive"
      : isAdls
        ? "Azure Data Lake Storage"
        : "S3";

  // Prepare params for suggest primary keys API
  const suggestPrimaryKeysParams = useMemo(() => {
    // Create mode: pendingFormData holds the full form fields
    if (pendingFormData?.form_data) {
      const formData = pendingFormData.form_data;
      return {
        isSftp,
        sourceType,
        s3_bucket:
          formData.s3_bucket ||
          formData.sftp_bucket ||
          formData.bucket ||
          formData.host,
        aws_access_key_id: formData.aws_access_key_id,
        aws_secret_access_key: formData.aws_secret_access_key,
        base_folder_path: formData.base_folder_path,
        file_type: formData.file_type,
        ...formData,
      };
    }
    // Edit mode: editPendingPayload holds the full submitted form; send it
    // together with the connection_id so the API has full context to analyse
    // only the pending (newly mapped) tables.
    if (editPendingPayload?.form_data && createdConnectionId) {
      const formData = editPendingPayload.form_data;
      return {
        isSftp,
        sourceType,
        connection_id: createdConnectionId,
        s3_bucket:
          formData.s3_bucket ||
          formData.sftp_bucket ||
          formData.bucket ||
          formData.host,
        aws_access_key_id: formData.aws_access_key_id,
        aws_secret_access_key: formData.aws_secret_access_key,
        base_folder_path: formData.base_folder_path,
        file_type: formData.file_type,
        // Tell the backend which tables still need primary key analysis.
        ...(pendingKeyTables.length > 0
          ? { pending_primary_key_tables: pendingKeyTables }
          : {}),
        ...formData,
      };
    }
    // Fallback: only connection_id available
    if (createdConnectionId) {
      return { connection_id: createdConnectionId, isSftp, sourceType };
    }
    return null;
  }, [
    pendingFormData,
    editPendingPayload,
    pendingKeyTables,
    createdConnectionId,
    isSftp,
    sourceType,
  ]);

  // Fetch suggested primary keys when showing primary key selection
  const { data: suggestedPrimaryKeys, isPending: isSuggestPrimaryKeysPending } =
    useSuggestPrimaryKeys(
      suggestPrimaryKeysParams ?? ({} as SuggestPrimaryKeysRequest),
      showPrimaryKeySelection,
    );

  // Ensure edit-mode primary key selection opens only after payload is ready.
  useEffect(() => {
    if (
      mode === "edit" &&
      editPendingPayload &&
      createdConnectionId &&
      !showPrimaryKeySelection
    ) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowPrimaryKeySelection(true);
    }
  }, [mode, editPendingPayload, createdConnectionId, showPrimaryKeySelection]);

  // Mutation hooks
  const {
    mutate: updateConnectorConfig,
    isPending: isUpdateConnectorConfigPending,
  } = useUpdateConnectorConfig({
    connectorId: shouldFetch ? Number(connectionId) : 0,
    type: connectorData?.source_name || "",
  });
  const { mutate: createConnection, isPending: isCreateConnectorPending } =
    useCreateConnection(sourceName);

  // ------------------- Strict-safe form submit -------------------
  const handleFormSubmit = (values: Record<string, unknown>) => {
    const connectionName =
      (values["connection_name"] as string) || "Unnamed Connector";
    const parsedValues: Record<string, unknown> = { ...values };
    const jsonFields = ["single_file_table_mapping", "table_to_files_mapping"];

    jsonFields.forEach((field) => {
      if (parsedValues[field] && typeof parsedValues[field] === "string") {
        try {
          parsedValues[field] = JSON.parse(parsedValues[field]);
        } catch (e) {
          console.warn(`\u26a0\ufe0f S3 - Failed to parse ${field}:`, e);
        }
      }
    });

    const stringifiedValues: Record<string, string> = {};
    Object.entries(parsedValues).forEach(([key, value]) => {
      if (value === undefined || value === null) {
        stringifiedValues[key] = "";
      } else if (typeof value === "object") {
        stringifiedValues[key] = JSON.stringify(value);
      } else {
        stringifiedValues[key] = String(value);
      }
    });

    // Ensure latest mapping fields are preserved as-is from the submitted values.
    if (values["single_file_table_mapping"] !== undefined) {
      stringifiedValues.single_file_table_mapping = String(
        values["single_file_table_mapping"] || "",
      );
    }
    if (values["table_to_files_mapping"] !== undefined) {
      stringifiedValues.table_to_files_mapping = String(
        values["table_to_files_mapping"] || "",
      );
    }
    if (values["multi_files_table_name"] !== undefined) {
      stringifiedValues.multi_files_table_name = String(
        values["multi_files_table_name"] || "",
      );
    }
    if (values["multi_files_prefix"] !== undefined) {
      stringifiedValues.multi_files_prefix = String(
        values["multi_files_prefix"] || "",
      );
    }

    const requiresPrimaryKeySelection =
      parsedValues["load_method"] === "upsert_custom_key" &&
      !(
        parsedValues["file_type"] === "json" &&
        parsedValues["json_mode"] === "packed"
      ) &&
      !(
        parsedValues["file_type"] === "xml" &&
        parsedValues["xml_mode"] === "packed"
      );

    const destinationSchema = resolveDestinationSchema(
      stringifiedValues,
      connectorConfig?.initial_data?.destination_schema,
    );

    if (mode === "create") {
      const createPayload: CreateConnectionPayload = {
        connection_name: connectionName,
        destination_schema: destinationSchema,
        form_data: stringifiedValues,
        ...(state?.destinationConfigId
          ? { destination_config_id: state.destinationConfigId }
          : {}),
      };

      if (requiresPrimaryKeySelection) {
        setPendingFormData({
          connection_name: createPayload.connection_name,
          destination_schema: createPayload.destination_schema,
          form_data: stringifiedValues,
          ...(createPayload.destination_config_id
            ? { destination_config_id: createPayload.destination_config_id }
            : {}),
        });
        setShowPrimaryKeySelection(true);
        return;
      }

      createConnection(createPayload, {
        onSuccess: (response) => {
          if (response.auth_url) {
            window.location.href = response.auth_url;
          } else {
            toaster.success({
              title:
                response.message ||
                `${displayName} Connector created successfully`,
              description: `The ${displayName} connector has been created.`,
            });
            navigate(
              `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
            );
          }
        },
      });
    } else {
      const editPayload = {
        connection_name: connectionName,
        destination_schema: destinationSchema,
        form_data: stringifiedValues,
      };

      // If user chose custom primary key method, show selection UI before updating
      if (requiresPrimaryKeySelection && !showPrimaryKeySelection) {
        setEditPendingPayload(editPayload);
        setCreatedConnectionId(Number(connectionId));
        // Defer opening until payload is stored so suggest-primary-keys uses latest mapping
        setShowPrimaryKeySelection(false);
        return;
      }

      updateConnectorConfig(editPayload, {
        onSuccess: (response) => {
          if (response.auth_url) {
            window.location.href = response.auth_url;
          } else if (response.requires_primary_key_selection) {
            // Backend says primary key selection is needed for newly mapped tables.
            setEditPendingPayload(editPayload);
            setCreatedConnectionId(
              response.connection_id ?? Number(connectionId),
            );
            setPendingKeyTables(response.pending_primary_key_tables ?? []);
            setShowPrimaryKeySelection(true);
          } else {
            toaster.success({
              title: `${displayName} Connector updated successfully`,
              description: `The ${displayName} connector has been updated.`,
            });
            refreshSchemaStatus(connectionId);
            navigate(
              `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/${connectionId}/${ClientRoutes.CONNECTORS.SETTINGS}`,
            );
          }
        },
      });
    }
  };
  // -----------------------------------------------------------------

  const refreshSchemaStatus = (id: number | string | null | undefined) => {
    const connectionIdNum = Number(id);
    if (!connectionIdNum) return;
    queryClient.removeQueries({ queryKey: ["SchemaStatus", connectionIdNum] });
    queryClient.removeQueries({ queryKey: ["tableFields", connectionIdNum] });
    queryClient.removeQueries({
      queryKey: ["ConnectorTable", connectionIdNum],
    });
    queryClient.removeQueries({
      queryKey: ["SelectedTables", connectionIdNum],
    });
    queryClient.removeQueries({ queryKey: ["ReverseSchema", connectionIdNum] });

    queryClient.invalidateQueries({
      queryKey: ["SchemaStatus", connectionIdNum],
      refetchType: "active",
    });
    queryClient.invalidateQueries({
      queryKey: ["TableStatus", connectionIdNum],
      refetchType: "active",
    });
    queryClient.invalidateQueries({
      queryKey: ["ConnectorTable", connectionIdNum],
      refetchType: "active",
    });
    queryClient.invalidateQueries({
      queryKey: ["SelectedTables", connectionIdNum],
      refetchType: "active",
    });
    queryClient.invalidateQueries({
      queryKey: ["tableFields", connectionIdNum],
      refetchType: "active",
    });
    queryClient.invalidateQueries({
      queryKey: ["ReverseSchema", connectionIdNum],
      refetchType: "active",
    });
  };

  // Get the schema fields - Priority: Backend Config (Edit) > Form Schema (Create)
  const baseSchemaFields = useMemo(() => {
    return mode === "edit"
      ? connectorConfig?.source_schema ||
          connectorConfig?.fields ||
          formSchema ||
          []
      : formSchema || [];
  }, [
    mode,
    connectorConfig?.source_schema,
    connectorConfig?.fields,
    formSchema,
  ]);

  const schemaFields = useMemo(() => {
    const fields = baseSchemaFields as S3FieldSchema[];
    if (!fields || fields.length === 0) return fields;

    // Check if include_subfolders is already in the schema
    const hasIncludeSubfolders = fields.some(
      (f) => f.name === "include_subfolders",
    );
    if (hasIncludeSubfolders) return fields;

    const includeSubfoldersField: S3FieldSchema = {
      name: "include_subfolders",
      label: "Include Subfolders",
      type: "CharField",
      required: true,
      widget: "Checkbox",
      is_visible: true,
      description: "Check to recursively search for files in subfolders.",
      choices: [
        { value: "true", display: "Yes" },
        { value: "false", display: "No" },
      ],
      default_value: "false",
    };

    // Find the index of the base folder path field to insert after it
    const baseFolderIndex = fields.findIndex(
      (f) =>
        f.name === "base_folder_path" ||
        f.name === "basefolder" ||
        f.name === "folder_path" ||
        f.label?.toLowerCase().includes("base folder") ||
        f.label?.toLowerCase().includes("folder path"),
    );

    if (baseFolderIndex !== -1) {
      const newSchema = [...fields];
      newSchema.splice(baseFolderIndex + 1, 0, includeSubfoldersField);
      return newSchema;
    }

    // Default to putting it after the first few fields or at the end
    const bucketIndex = fields.findIndex((f) =>
      f.name.toLowerCase().includes("bucket"),
    );
    if (bucketIndex !== -1) {
      const newSchema = [...fields];
      newSchema.splice(bucketIndex + 1, 0, includeSubfoldersField);
      return newSchema;
    }

    return [...fields, includeSubfoldersField];
  }, [baseSchemaFields]);

  const s3DefaultValues = useMemo(
    () =>
      mode === "edit" && connectorConfig
        ? { ...connectorConfig.initial_data }
        : pendingFormData?.form_data || undefined,
    [mode, connectorConfig, pendingFormData?.form_data],
  );

  // ------------------- Loading state -------------------
  if (
    (mode === "create" && (isLoading || !formSchema)) ||
    (mode === "edit" &&
      (isFetchConnectorByIdPending ||
        isFetchConnectorConfigPending ||
        (!connectorConfig?.source_schema && (isLoading || !formSchema))))
  ) {
    return <LoadingSpinner />;
  }

  // ------------------- Primary key selection -------------------
  if (showPrimaryKeySelection) {
    if (pendingFormData) {
      if (isSuggestPrimaryKeysPending) return <LoadingSpinner />;

      const formData = pendingFormData.form_data;
      const schemaData = suggestedPrimaryKeys?.tables
        ? {
            schemaName:
              formData.multi_files_table_name ||
              formData.destination_schema ||
              "Schema",
            tables: (() => {
              const tablesFromApi = suggestedPrimaryKeys.tables.map(
                (table) => ({
                  name: table.table_name,
                  pkLocked: table.pk_locked,
                  columns: table.columns.map((col) => ({
                    name: col.column_name,
                    isPrimaryKey:
                      table.existing_primary_keys &&
                      table.existing_primary_keys.length > 0
                        ? table.existing_primary_keys.includes(col.column_name)
                        : (col.is_selected ?? col.is_suggested_pk),
                    cardinality: col.uniqueness_score / 100,
                    warning:
                      col.warnings.length > 0
                        ? col.warnings.join(". ")
                        : undefined,
                  })),
                }),
              );

              const mappedTables: string[] = [];
              try {
                const mappingRaw = formData.single_file_table_mapping;
                if (mappingRaw) {
                  const parsed = JSON.parse(mappingRaw) as Record<
                    string,
                    string
                  >;
                  Object.values(parsed).forEach((tableName) => {
                    if (tableName) mappedTables.push(tableName);
                  });
                }
              } catch {
                // ignore parse errors
              }

              const existing = new Set(
                tablesFromApi.map((t) => t.name.toLowerCase()),
              );
              mappedTables.forEach((tableName) => {
                const normalized = tableName.toLowerCase();
                if (!existing.has(normalized)) {
                  tablesFromApi.push({
                    name: tableName,
                    pkLocked: false,
                    columns: [],
                  });
                  existing.add(normalized);
                }
              });

              return tablesFromApi;
            })(),
          }
        : undefined;

      return (
        <PrimaryKeySelection
          schemaData={schemaData}
          loading={isCreateConnectorPending}
          onBack={() => {
            setShowPrimaryKeySelection(false);
          }}
          onSaveAndContinue={(primaryKeys) => {
            const formDataWithPrimaryKeys = {
              ...pendingFormData,
              form_data: {
                ...formData,
                custom_primary_key: JSON.stringify(primaryKeys),
              },
            };

            createConnection(
              formDataWithPrimaryKeys as unknown as CreateConnectionPayload,
              {
                onSuccess: () => {
                  toaster.success({
                    title: `${displayName} Connector created successfully`,
                    description: "Primary keys have been configured.",
                  });
                  navigate(
                    `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
                  );
                },
              },
            );
          }}
        />
      );
    }

    if (createdConnectionId) {
      if (isSuggestPrimaryKeysPending) return <LoadingSpinner />;

      const schemaData = suggestedPrimaryKeys?.tables
        ? {
            schemaName: "Schema",
            tables: (() => {
              const tablesFromApi = suggestedPrimaryKeys.tables.map(
                (table) => ({
                  name: table.table_name,
                  pkLocked: table.pk_locked,
                  columns: table.columns.map((col) => ({
                    name: col.column_name,
                    isPrimaryKey:
                      table.existing_primary_keys &&
                      table.existing_primary_keys.length > 0
                        ? table.existing_primary_keys.includes(col.column_name)
                        : (col.is_selected ?? col.is_suggested_pk),
                    cardinality: col.uniqueness_score / 100,
                    warning:
                      col.warnings.length > 0
                        ? col.warnings.join(". ")
                        : undefined,
                  })),
                }),
              );

              const mappedTables: string[] = [];
              try {
                const mappingRaw =
                  editPendingPayload?.form_data.single_file_table_mapping;
                if (mappingRaw) {
                  const parsed = JSON.parse(mappingRaw) as Record<
                    string,
                    string
                  >;
                  Object.values(parsed).forEach((tableName) => {
                    if (tableName) mappedTables.push(tableName);
                  });
                }
              } catch {
                // ignore parse errors
              }

              const existing = new Set(
                tablesFromApi.map((t) => t.name.toLowerCase()),
              );
              mappedTables.forEach((tableName) => {
                const normalized = tableName.toLowerCase();
                if (!existing.has(normalized)) {
                  tablesFromApi.push({
                    name: tableName,
                    pkLocked: false,
                    columns: [],
                  });
                  existing.add(normalized);
                }
              });

              return tablesFromApi;
            })(),
          }
        : undefined;

      // Edit mode: re-submit config with chosen primary keys
      if (mode === "edit" && editPendingPayload) {
        return (
          <PrimaryKeySelection
            schemaData={schemaData}
            loading={isUpdateConnectorConfigPending}
            onBack={() => {
              setShowPrimaryKeySelection(false);
              setCreatedConnectionId(null);
              setEditPendingPayload(null);
              setPendingKeyTables([]);
            }}
            onSaveAndContinue={(primaryKeys) => {
              // Merge existing custom_primary_key (for already-configured tables)
              // with the newly selected keys (for pending tables like LOGS).
              // New selections win on any key collision.
              let existingPrimaryKeys: Record<string, string[]> = {};
              try {
                const raw = editPendingPayload.form_data.custom_primary_key;
                if (raw) {
                  if (typeof raw === "string") {
                    existingPrimaryKeys = JSON.parse(raw) as Record<
                      string,
                      string[]
                    >;
                  } else if (typeof raw === "object") {
                    existingPrimaryKeys = raw as Record<string, string[]>;
                  }
                }
              } catch {
                // ignore parse errors; fall back to empty
              }
              const mergedPrimaryKeys: Record<string, string[]> = {
                ...existingPrimaryKeys,
                ...primaryKeys,
              };
              updateConnectorConfig(
                {
                  ...editPendingPayload,
                  form_data: {
                    ...editPendingPayload.form_data,
                    custom_primary_key: JSON.stringify(mergedPrimaryKeys),
                  },
                },
                {
                  onSuccess: () => {
                    toaster.success({
                      title: `${displayName} Connector updated successfully`,
                      description: "Primary keys have been configured.",
                    });
                    setShowPrimaryKeySelection(false);
                    setCreatedConnectionId(null);
                    setEditPendingPayload(null);
                    setPendingKeyTables([]);
                    refreshSchemaStatus(connectionId);
                    navigate(
                      `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/${connectionId}/${ClientRoutes.CONNECTORS.SETTINGS}`,
                    );
                  },
                },
              );
            }}
          />
        );
      }

      // Create mode: navigate away after selecting keys
      return (
        <PrimaryKeySelection
          schemaData={schemaData}
          onBack={() => {
            setShowPrimaryKeySelection(false);
            setCreatedConnectionId(null);
          }}
          onSaveAndContinue={() => {
            toaster.success({
              title: "Primary keys configured",
              description: `Your ${displayName} connector is now ready to use.`,
            });
            navigate(
              `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
            );
          }}
        />
      );
    }
  }

  // ------------------- Main form -------------------
  return (
    <Box
      w="full"
      h={{ base: "auto", md: "calc(100vh - 64px)" }}
      mt={{ base: 0, md: -6 }}
      mb={{ base: 0, md: -6 }}
      mr={{ base: 0, md: -6 }}
      overflow="hidden"
    >
      <Grid
        templateColumns={{
          base: "1fr",
          xl: "1fr 1fr",
        }}
        templateRows={{
          base: "1fr 1fr",
          xl: "1fr",
        }}
        alignItems="stretch"
        gap={0}
        w="full"
        h="full"
      >
        <Box
          w="full"
          h="full"
          overflowY="auto"
          overscrollBehaviorY="contain"
          pt={{ base: 0, md: 6 }}
          pb={{ base: 4, md: 6 }}
          pr={{ base: 0, xl: 4 }}
        >
          <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
            <PageHeader
              breadcrumbs={[
                {
                  label: "Connector",
                  route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
                },
                ...(mode === "edit"
                  ? [
                      {
                        label: "Settings",
                        route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/${connectionId}/${ClientRoutes.CONNECTORS.SETTINGS}`,
                      },
                    ]
                  : []),
                {
                  label:
                    mode === "edit"
                      ? `Edit ${displayName} Connector`
                      : `Configure ${displayName}`,
                },
              ]}
              title={
                mode === "edit"
                  ? `Edit ${displayName} Connector`
                  : `Enter ${displayName} authorization details`
              }
              subtitle={
                mode === "create"
                  ? `Provide the necessary details to authorize the ${displayName} connector`
                  : undefined
              }
            >
              {mode === "edit" && (
                <Flex align="center" gap={4} wrap="wrap">
                  <Text fontSize="sm">
                    Modify the {displayName} connector configuration
                  </Text>
                  <ModifiedAuditInfo connector={connectorData} />
                </Flex>
              )}
            </PageHeader>
            <S3DynamicForm
              schema={schemaFields as S3FieldSchema[]}
              onSubmit={handleFormSubmit}
              loading={
                isCreateConnectorPending || isUpdateConnectorConfigPending
              }
              handleBackButtonClick={handlePrevious}
              defaultValues={s3DefaultValues}
              mode={mode}
              sourceName={sourceName}
              isSftp={isSftp}
              connectionId={shouldFetch ? Number(connectionId) : undefined}
              migrationStatus={connectorData?.migration_status}
            />
          </Flex>
        </Box>

        <Box
          w="full"
          h="full"
          overflow="hidden"
          bg={{ base: "transparent", xl: "gray.50" }}
        >
          {isSftp || isGoogleDrive || isAdls ? (
            <ConnectorDocsHelperPanel
              connectorKey={
                isGoogleDrive
                  ? "googledrive"
                  : isAdls
                    ? "azuredatalakestorage"
                    : "sftp"
              }
              kind="connector"
            />
          ) : (
            <S3DocsHelperPanel />
          )}
        </Box>
      </Grid>
    </Box>
  );
};

export default S3ConnectorConfiguration;
