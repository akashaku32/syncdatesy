import { startTransition, useCallback, useEffect, useState } from "react";

import { Badge, Flex, HStack, Image, Text } from "@chakra-ui/react";

import { useNavigate, useSearchParams } from "react-router";

import TableWrapper from "@/components/dashboard/wrapper/TableWrapper";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import usePermissions from "@/hooks/usePermissions";
import { useFetchDestinationListByPage } from "@/queryOptions/destination/useFetchDestinationListByPage";
import Table, { type Column } from "@/shared/Table";
import { type DestinationTableItem } from "@/types/destination";

import { getDestinationImage } from "../../utils/getImage";
import PageHeader from "../../wrapper/PageHeader";
import TableFilter from "../../wrapper/TableFilter";
import NoDestinations from "./components/NoDestination";
import { formatDestinationDate } from "./destinationTableFormat";

type AuditUser = NonNullable<DestinationTableItem["modified_by"]>;

const getFirstName = (user?: AuditUser | null) => {
  if (!user) return "";
  if (typeof user === "string") return user.trim().split(/\s+/)[0] || "";
  return user.first_name || "";
};

const getModifiedByName = (destination: DestinationTableItem) =>
  getFirstName(destination.modified_by) ||
  getFirstName(destination.updated_by) ||
  getFirstName(destination.modified_by_name) ||
  getFirstName(destination.updated_by_name) ||
  "";

const getCreatedByName = (destination: DestinationTableItem) =>
  getFirstName(destination.created_by) ||
  getFirstName(destination.created_by_name) ||
  "";

const columns: Column<DestinationTableItem>[] = [
  { header: "Name", accessor: "name" },
  {
    header: "Destination",
    accessor: "dst",
    render: (_, { dst }) => (
      <HStack gap={1} align="center">
        <Image
          src={getDestinationImage(dst)}
          alt={dst}
          boxSize="24px"
          objectFit="contain"
        />
        <Text fontSize="sm">{dst}</Text>
      </HStack>
    ),
  },
  {
    header: "Created At",
    accessor: "created_at",
    render: (_, { created_at }) => formatDestinationDate(created_at),
  },
  {
    header: "Modified By",
    accessor: "modified_by",
    render: (_, destination) => (
      <Text fontSize="sm">{getModifiedByName(destination) || "--"}</Text>
    ),
  },
  {
    header: "Created By",
    accessor: "created_by",
    render: (_, destination) => (
      <Text fontSize="sm">{getCreatedByName(destination) || "--"}</Text>
    ),
  },
  {
    header: "Modified At",
    accessor: "modified_at",
    render: (_, { modified_at, updated_at }) =>
      formatDestinationDate(modified_at || updated_at),
  },
  {
    header: "Status",
    accessor: "is_active",
    render: (_, { is_active }) => (
      <Badge
        colorPalette={is_active ? "green" : "red"}
        variant="solid"
        size="sm"
      >
        {is_active ? "Active" : "Paused"}
      </Badge>
    ),
  },
];
const SIZE = 10;

const Destination = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const oauthStatus = searchParams.get("oauth_status");
    const oauthError = searchParams.get("oauth_error");

    if (oauthStatus === "error" && oauthError) {
      toaster.error({
        title: oauthError,
      });

      // Remove query params from the URL using replaceState
      const url = new URL(window.location.href);
      url.searchParams.delete("oauth_status");
      url.searchParams.delete("oauth_error");
      window.history.replaceState(
        {},
        document.title,
        url.pathname + url.search,
      );
    }
  }, [searchParams]);
  const [currentPage, setCurrentPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const { can } = usePermissions();

  const canCreate = can("can_create_destinations");
  const canEdit = can("can_edit_destinations");

  const { data, isLoading } = useFetchDestinationListByPage({
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

  return (
    <Flex flexDirection="column" height="100%" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={[{ label: "Destinations", route: "" }]}
        title="Destinations"
        buttonLabel={canCreate ? "Add Destination" : undefined}
        onCreateClick={
          canCreate ? () => navigate(ClientRoutes.DESTINATION.ADD) : undefined
        }
      />
      {data?.totalElements === 0 && !searchTerm && <NoDestinations />}
      <TableFilter
        handleSearchInputChange={(e) => setSearchTerm(e.target.value)}
      />
      <TableWrapper>
        <Table<DestinationTableItem>
          key={searchTerm}
          data={data?.content || []}
          columns={columns}
          updateCurrentPage={updateCurrentPage}
          totalElements={data?.totalElements || 0}
          pageSize={SIZE}
          isLoading={isLoading}
          onRowClick={
            canEdit
              ? (row) =>
                  navigate(
                    `${ClientRoutes.DESTINATION.EDIT}/${row.dst_config_id}`,
                  )
              : undefined
          }
        />
      </TableWrapper>
    </Flex>
  );
};

export default Destination;
