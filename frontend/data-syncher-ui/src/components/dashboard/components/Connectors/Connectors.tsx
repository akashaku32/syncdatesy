import { startTransition, useCallback, useEffect, useState } from "react";

import { Badge, Flex, HStack, Image, Text } from "@chakra-ui/react";

import { format } from "date-fns";
import { useNavigate } from "react-router";

import CheckIcon from "@/assets/icons/check-icon.svg";
import ErrorIcon from "@/assets/icons/error-icon.svg";
import TableWrapper from "@/components/dashboard/wrapper/TableWrapper";
import LoadingSpinner from "@/components/shared/Spinner";
import { Tooltip } from "@/components/ui/tooltip";
import ClientRoutes from "@/constants/client-routes";
import { dateTimeFormat } from "@/constants/common";
import useAuth from "@/context/Auth/useAuth";
import useCompanyConnectionsStatusWS from "@/hooks/useCompanyConnectionsStatusWS";
import usePermissions from "@/hooks/usePermissions";
import { useFetchConnectorsListByPage } from "@/queryOptions/connector/useFetchConnectorsListByPage";
import Table, { type Column } from "@/shared/Table";
import type { ConnectorStatus, ConnectorTableItem } from "@/types/connectors";

import { getDestinationImage, getSourceImage } from "../../utils/getImage";
import PageHeader from "../../wrapper/PageHeader";
import TableFilter from "../../wrapper/TableFilter";
import NoConnections from "./components/NoConnections";

type AuditUser = NonNullable<ConnectorTableItem["modified_by"]>;

const getFirstName = (user?: AuditUser | null) => {
  if (!user) return "";
  if (typeof user === "string") return user.trim().split(/\s+/)[0] || "";
  return user.first_name || "";
};

const getModifiedByName = (connector: ConnectorTableItem) =>
  getFirstName(connector.modified_by) ||
  getFirstName(connector.updated_by) ||
  getFirstName(connector.modified_by_name) ||
  getFirstName(connector.updated_by_name) ||
  "";

const getStatusColor = (status: ConnectorStatus) => {
  switch (status) {
    case "A":
      return "green";
    case "P":
      return "yellow";
    default:
      return "gray";
  }
};

const columns: Column<ConnectorTableItem>[] = [
  { header: "Connector Name", accessor: "connector_name" },
  {
    header: "Source",
    accessor: "source_name",
    render: (_, { source_name, display_name }) => (
      <HStack gap={1} align="center">
        <Image
          src={getSourceImage(source_name)}
          alt={source_name}
          boxSize="24px"
          objectFit="contain"
        />
        <Text fontSize="xs">{display_name || source_name}</Text>
      </HStack>
    ),
  },
  {
    header: "Destination Name",
    accessor: "dst_config_name",
  },
  {
    header: "Destination",
    accessor: "destination_name",
    render: (_, { destination_name }) => (
      <HStack gap={1} align="center">
        <Image
          src={getDestinationImage(destination_name)}
          alt={destination_name}
          boxSize="24px"
          objectFit="contain"
        />
        <Text fontSize="xs">{destination_name}</Text>
      </HStack>
    ),
  },
  {
    header: "Modified By",
    accessor: "modified_by",
    render: (_, connector) => (
      <Text fontSize="xs">{getModifiedByName(connector) || "--"}</Text>
    ),
  },
  {
    header: "Last sync",
    accessor: "last_synced_new",
    render: (_, { last_synced_new }) => {
      const d = new Date(last_synced_new as string | number);
      if (Number.isNaN(d.getTime())) return String(last_synced_new ?? "");
      return format(d, dateTimeFormat);
    },
  },
  {
    header: "Migration status",
    accessor: "migration_status",
    textAlign: "center",
    width: "80px",
    render: (_, { migration_status, error_message }) => {
      return (
        <HStack gap={2} w="100%" justify="center">
          {migration_status === "S" && (
            <Tooltip content="Sync in progress" showArrow>
              <LoadingSpinner
                size="sm"
                containerProps={{ h: "16px", w: "16px" }}
                spinnerProps={{ color: "blue.500" }}
              />
            </Tooltip>
          )}
          {migration_status === "E" && (
            <Tooltip
              content={error_message || "Migration failed"}
              disabled={!error_message}
              showArrow
            >
              <Image
                src={ErrorIcon}
                w="16px"
                h="16px"
                objectFit="contain"
                cursor={error_message ? "pointer" : "default"}
              />
            </Tooltip>
          )}
          {migration_status === "C" && (
            <Image src={CheckIcon} w="16px" h="16px" objectFit="contain" />
          )}
        </HStack>
      );
    },
  },

  {
    header: "Connection status",
    accessor: "status",
    textAlign: "center",
    width: "120px",
    render: (_, { status }) => (
      <Badge colorPalette={getStatusColor(status)} variant="solid" size="sm">
        {(() => {
          switch (status) {
            case "A":
              return "Active";
            case "P":
              return "Paused";
            default:
              return "Unknown";
          }
        })()}
      </Badge>
    ),
  },
];
const SIZE = 10;

const Connectors = () => {
  const navigate = useNavigate();
  const [currentPage, setCurrentPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const { authState } = useAuth();
  const { can } = usePermissions();

  const cmpId = authState.user?.company?.cmp_id ?? null;
  useCompanyConnectionsStatusWS(cmpId);

  const canCreate = can("can_create_connectors");

  const { data, isLoading } = useFetchConnectorsListByPage({
    page: currentPage,
    size: SIZE,
    searchTerm,
  });

  useEffect(() => {
    startTransition(() => {
      setCurrentPage(1);
    });
  }, [searchTerm]);

  const updateCurrentPage = useCallback((page: number) => {
    setCurrentPage(page);
  }, []);

  //if (data?.totalElements === 0) return <NoConnections />;
  return (
    <Flex flexDirection="column" gap={4} h="100%">
      <PageHeader
        breadcrumbs={[
          {
            label: "Connectors",
            route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
          },
        ]}
        title="Connectors"
        buttonLabel={canCreate ? "Add Connector" : undefined}
        onCreateClick={
          canCreate ? () => navigate(ClientRoutes.CONNECTORS.ADD) : undefined
        }
      />

      {data?.totalElements === 0 && !searchTerm && <NoConnections />}
      <TableFilter
        handleSearchInputChange={(e) => setSearchTerm(e.target.value)}
      />
      <TableWrapper>
        <Table<ConnectorTableItem>
          key={searchTerm}
          data={data?.content || []}
          columns={columns}
          totalElements={data?.totalElements || 0}
          pageSize={SIZE}
          updateCurrentPage={updateCurrentPage}
          isLoading={isLoading}
          onRowClick={(row) =>
            navigate(`${ClientRoutes.CONNECTORS.EDIT}/${row.connection_id}`)
          }
        />
      </TableWrapper>
    </Flex>
  );
};

export default Connectors;
