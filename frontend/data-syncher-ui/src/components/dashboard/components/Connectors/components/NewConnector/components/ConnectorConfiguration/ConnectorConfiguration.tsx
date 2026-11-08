import { useMemo } from "react";

import { Box, Flex, Grid, Text } from "@chakra-ui/react";

import { format } from "date-fns";
import { useNavigate, useParams } from "react-router";

import DynamicForm from "@/components/dashboard/helpers/DynamicForm";
import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import { dateTimeFormat } from "@/constants/common";
import { VIEW_CONFIG } from "@/constants/view-config";
import useCreateConnection from "@/queryOptions/connector/useCreateConnection";
import useFetchConnectorConfig from "@/queryOptions/connector/useFetchConnectorConfig";
import { useFetchConnectorById } from "@/queryOptions/connector/useFetchConnectorDetailsById";
import useUpdateConnectorConfig from "@/queryOptions/connector/useUpdateConnectorConfig";
import useFetchAllUserCreatedDestinationList from "@/queryOptions/destination/useFetchAllUserCreatedDestinationList";
import useFetchFormSchema from "@/queryOptions/useFetchFormSchema";
import { type AuditUser, type Connector } from "@/types/connectors";

import { type ConnectorFormState } from "../../type";
import ConnectorDocsHelperPanel from "./ConnectorDocsHelperPanel";
import S3ConnectorConfiguration from "./S3ConnectorConfiguration";
import { resolveDestinationSchema } from "./resolveDestinationSchema";

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
    <Flex gap={3} align="center" wrap="wrap">
      <Flex gap={1}>
        <Text fontSize="sm">Modified by:</Text>
        <Text fontSize="sm" fontWeight="semibold">
          {getModifiedByName(connector) || "--"}
        </Text>
      </Flex>
      <Box w="1px" h="14px" bg="gray.300" />
      <Flex gap={1}>
        <Text fontSize="sm">Modified at:</Text>
        <Text fontSize="sm" fontWeight="semibold">
          {formatDateTime(connector.modified_at)}
        </Text>
      </Flex>
    </Flex>
  );
};

/**
 * Generic configuration component for all other connectors
 */
const GenericConnectorConfiguration = ({
  state,
  handlePrevious,
  mode = "create",
}: {
  state?: ConnectorFormState;
  handlePrevious?: () => void;
  mode: "create" | "edit";
}) => {
  const navigate = useNavigate();
  const { connectionId } = useParams<{ connectionId: string }>();
  const shouldFetch = mode === "edit" && !!connectionId;

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

  const { data: destinationList } = useFetchAllUserCreatedDestinationList();

  const destinationType = useMemo(() => {
    if (mode === "edit" && connectorConfig?.destination_config) {
      return connectorConfig.destination_config.dst;
    }
    if (mode === "create" && state?.destination && destinationList) {
      const match = destinationList.find((d) => d.name === state.destination);
      return match?.dst || "";
    }
    return "";
  }, [mode, connectorConfig, state, destinationList]);

  // Mutation hooks
  const {
    mutate: updateConnectorConfig,
    isPending: isUpdateConnectorConfigPending,
  } = useUpdateConnectorConfig({
    connectorId: shouldFetch ? Number(connectionId) : 0,
    type: connectorData?.source_name || "",
  });
  const { mutate: createConnection, isPending: isCreateConnectorPending } =
    useCreateConnection(state?.source || "");

  const handleFormSubmit = (values: Record<string, string>) => {
    const destinationSchema = resolveDestinationSchema(
      values,
      connectorConfig?.initial_data?.destination_schema,
    );

    if (mode === "create") {
      createConnection(
        {
          connection_name: values.connection_name || "Unnamed Connector",
          destination_schema: destinationSchema,
          form_data: values,
          ...(state?.destinationConfigId
            ? { destination_config_id: state.destinationConfigId }
            : {}),
        },
        {
          onSuccess: (response) => {
            if (response.auth_url) {
              window.location.href = response.auth_url;
            } else {
              toaster.success({
                title: response.message || "Connector created successfully",
                description: "The connector has been created.",
              });
              navigate(
                `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
              );
            }
          },
        },
      );
    } else {
      updateConnectorConfig(
        {
          connection_name: values.connection_name || "Unnamed Connector",
          destination_schema: destinationSchema,
          form_data: values,
        },
        {
          onSuccess: (response) => {
            if (response.auth_url) {
              window.location.href = response.auth_url;
            } else {
              toaster.success({
                title: "Connector updated successfully",
                description: "The connector has been updated.",
              });
              navigate(
                `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/${connectionId}/${ClientRoutes.CONNECTORS.SETTINGS}`,
              );
            }
          },
        },
      );
    }
  };

  // Determine the source name
  const sourceName = state?.source || connectorData?.source_name || "";

  // Get the schema fields
  const schemaFields =
    mode === "edit"
      ? connectorConfig?.source_schema ||
        connectorConfig?.fields ||
        formSchema ||
        []
      : formSchema || [];

  const defaultValues = useMemo(
    () =>
      mode === "edit" && connectorConfig
        ? { ...connectorConfig.initial_data }
        : undefined,
    [mode, connectorConfig],
  );

  if (
    (mode === "create" && (isLoading || !formSchema)) ||
    (mode === "edit" &&
      (isFetchConnectorByIdPending ||
        isFetchConnectorConfigPending ||
        (!connectorConfig?.source_schema && (isLoading || !formSchema))))
  ) {
    return <LoadingSpinner />;
  }

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
        templateColumns={{ base: "1fr", xl: "1fr 1fr" }}
        templateRows={{ base: "1fr 1fr", xl: "1fr" }}
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
                { label: mode === "edit" ? "Edit Connector" : "Configure" },
              ]}
              title={
                mode === "edit"
                  ? "Edit Connector"
                  : "Enter authorization details"
              }
              subtitle={
                mode === "create"
                  ? "Provide the necessary details to authorize the connector"
                  : undefined
              }
            >
              {mode === "edit" && (
                <Flex align="center" gap={3} wrap="wrap">
                  <Text fontSize="sm" color="gray.600">
                    Modify the connector configuration
                  </Text>
                  {connectorData && (
                    <>
                      <Box w="1px" h="14px" bg="gray.300" />
                      <ModifiedAuditInfo connector={connectorData} />
                    </>
                  )}
                </Flex>
              )}
            </PageHeader>
            <DynamicForm
              mode={mode}
              config={{
                fields: schemaFields,
              }}
              sourceName={sourceName}
              destinationName={
                state?.destination || connectorConfig?.destination_config.name
              }
              destinationType={destinationType}
              onSubmit={(values) => {
                handleFormSubmit(values);
              }}
              loading={
                isCreateConnectorPending || isUpdateConnectorConfigPending
              }
              handleBackButtonClick={handlePrevious}
              defaultValues={defaultValues}
            />
          </Flex>
        </Box>

        <Box
          w="full"
          h="full"
          overflow="hidden"
          bg={{ base: "transparent", xl: "gray.50" }}
        >
          <ConnectorDocsHelperPanel
            connectorKey={sourceName}
            kind="connector"
          />
        </Box>
      </Grid>
    </Box>
  );
};

/**
 * Dispatcher component that routes to the appropriate configuration component
 * based on the connector type.
 */
const ConnectorConfiguration = (props: {
  state?: ConnectorFormState;
  handlePrevious?: () => void;
  mode: "create" | "edit";
}) => {
  const { connectionId } = useParams<{ connectionId: string }>();
  const shouldFetch = props.mode === "edit" && !!connectionId;

  // Fetch connector details to determine the type in edit mode
  const { data: connectorData, isPending: isFetchConnectorByIdPending } =
    useFetchConnectorById(shouldFetch ? Number(connectionId) : 0);

  // Determine the source name
  const sourceName = props.state?.source || connectorData?.source_name || "";

  // Determine if this source should use the file-based connector flow.
  const normalizedSourceName = sourceName
    ?.toLowerCase()
    .replace(/[\s\-._]/g, "");
  const isS3Connector =
    normalizedSourceName === "amazons3" ||
    normalizedSourceName === "sftp" ||
    normalizedSourceName === "googledrive" ||
    normalizedSourceName === "azuredatalakestorage" ||
    normalizedSourceName === "adls";

  if (shouldFetch && isFetchConnectorByIdPending) {
    return <LoadingSpinner />;
  }

  if (isS3Connector) {
    return <S3ConnectorConfiguration {...props} />;
  }

  return <GenericConnectorConfiguration {...props} />;
};

export default ConnectorConfiguration;
