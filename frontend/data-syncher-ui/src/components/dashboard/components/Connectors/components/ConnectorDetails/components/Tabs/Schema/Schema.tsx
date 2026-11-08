import { useEffect, useMemo, useRef, useState } from "react";

import {
  ActionBar,
  Box,
  Button,
  Checkbox,
  Flex,
  For,
  Grid,
  Image,
  Input,
  InputGroup,
  Portal,
  Skeleton,
  Text,
} from "@chakra-ui/react";

import { GoPlus } from "react-icons/go";
import { GrRefresh } from "react-icons/gr";
import { IoMdPlay } from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";
import { SlRefresh } from "react-icons/sl";
import { TbDelta } from "react-icons/tb";

import { useOutletContext } from "react-router";

import SandtimeIcon from "@/assets/icons/sand-time-icon.svg";
import Pagination from "@/components/shared/Pagination";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import { queryClient } from "@/lib/react-query-client";
import { useFetchBatches } from "@/queryOptions/connector/schema/useBatches";
import useFetchConnectorTableById from "@/queryOptions/connector/schema/useFetchTable";
import useFetchTableFields from "@/queryOptions/connector/schema/useFetchTableFields";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import { usePagination } from "@/queryOptions/connector/schema/usePagination";
import useRefreshDeltaTable from "@/queryOptions/connector/schema/useRefreshDeltaTable";
import useReloadSingleTable from "@/queryOptions/connector/schema/useReloadSingleTable";
import useUpdateSchemaStatus from "@/queryOptions/connector/schema/useUpdateSchemaStatus";
import useUpdateSelectedTables from "@/queryOptions/connector/schema/useUpdateSelectedTables";
import {
  type Connector,
  type ConnectorTable,
  type UnassignedTable,
} from "@/types/connectors";

import { isPrimaryKey } from "../ReverseSchema/utils/validation";
import Actions from "./Actions";
import BatchGroupedPanel from "./Batches/BatchGroupedPanel";
import SAPODataSchema from "./SAPODataSchema";
import { isConnectorTableMarkedSelected } from "./schemaSelection";
import { useIsMutating } from "@tanstack/react-query";

interface TableRowProps {
  item: ConnectorTable;
  index: number;
  connectionId: number;
  isExpanded: boolean;
  onToggleExpand: (_table: string) => void;
  /** Aligned with server selection or local overrides (see Schema `effectiveSelectedByTable`). */
  isSelected: boolean;
  onCheckedChange: (_checked: boolean) => void;
  reloadingTables: string[];
  refreshingTables: string[];
  shouldLockAllReloads: boolean;
  shouldLockAllRefreshes: boolean;
  tableStatusData?: {
    tables: Array<{ table: string; status?: string | null }>;
  };
  onReload: () => void;
  onRefresh: () => void;
  batchName?: string | null;
  /** Tables assigned to a batch are treated as selected but cannot be unchecked here. */
  selectionLocked?: boolean;
}
const TableRow = ({
  item,
  index,
  connectionId,
  isExpanded,
  onToggleExpand,
  isSelected,
  onCheckedChange,
  reloadingTables,
  refreshingTables,
  shouldLockAllReloads,
  shouldLockAllRefreshes,
  tableStatusData,
  onReload,
  onRefresh,
  batchName,
  selectionLocked = false,
}: TableRowProps) => {
  const { table } = item;
  const isEven = index % 2 === 0;
  const inBatch = !!batchName;
  const rowBg = inBatch ? "brand.50" : isEven ? "gray.100" : "white";

  const { data: tableFieldsData } = useFetchTableFields(
    connectionId,
    table,
    isExpanded,
  );

  const isThisTableReloading =
    reloadingTables?.some((t) => t.toLowerCase() === table.toLowerCase()) ??
    false;

  const isThisTableRefreshing =
    refreshingTables?.some((t) => t.toLowerCase() === table.toLowerCase()) ??
    false;

  const isThisTableInProgress =
    tableStatusData?.tables?.some(
      (t) => t.table === table && t.status === "in_progress",
    ) ?? false;

  const isReloadButtonDisabled =
    shouldLockAllReloads || isThisTableReloading || isThisTableInProgress;

  const isRefreshButtonDisabled =
    shouldLockAllRefreshes ||
    isThisTableRefreshing ||
    isThisTableInProgress ||
    isThisTableReloading ||
    !item.is_delta;

  return (
    <Flex
      key={table}
      backgroundColor={rowBg}
      direction="column"
      padding={2}
      borderRadius={4}
      borderLeftWidth={inBatch ? 3 : 0}
      borderLeftColor={inBatch ? "brand.500" : "transparent"}
    >
      <Grid
        templateColumns="24px 1fr auto"
        alignItems="start"
        width="100%"
        gap={2}
      >
        <Box>
          <Box onClick={() => onToggleExpand(table)} cursor="pointer">
            {isExpanded ? <IoCaretDownSharp /> : <IoMdPlay />}
          </Box>
        </Box>

        <Box>
          <Flex alignItems="center" gap={2}>
            <Text
              fontSize="sm"
              cursor="pointer"
              onClick={() => onToggleExpand(table)}
            >
              {table}
            </Text>
            {batchName && (
              <Text
                as="span"
                fontSize="2xs"
                fontWeight="semibold"
                color="brand.700"
                bgColor="brand.100"
                px={1.5}
                py={0.5}
                borderRadius="sm"
                maxW="140px"
                truncate
                title={batchName}
              >
                {batchName}
              </Text>
            )}
          </Flex>

          {isExpanded && (
            <Flex direction="column" gap={2} mt={2}>
              {tableFieldsData?.table_fields &&
                Object.entries(tableFieldsData.table_fields).map(
                  ([field, fieldInfo]) => {
                    const dataType =
                      typeof fieldInfo === "string"
                        ? fieldInfo
                        : (fieldInfo as { data_type?: string }).data_type ||
                          "unknown";

                    const isPK = isPrimaryKey(field, fieldInfo);

                    return (
                      <Flex key={field} gap={2} alignItems="center">
                        {isPK && <Text color="yellow.600">🔑</Text>}
                        <Text fontSize="sm">{field}</Text>
                        <Text color="gray.500">:</Text>
                        <Text fontSize="sm" color="gray.600">
                          {dataType}
                        </Text>
                      </Flex>
                    );
                  },
                )}
            </Flex>
          )}
        </Box>

        <Flex gap={6} alignItems="center" justifySelf="end">
          <Flex justifyContent="center" minW="40px">
            {item.is_delta && (
              <TbDelta color="#2563EB" size={18} title="Delta table" />
            )}
          </Flex>

          <Flex justifyContent="center" minW="40px">
            <Tooltip
              content={
                isRefreshButtonDisabled
                  ? !item.is_delta
                    ? "Refresh is only available for delta tables"
                    : "Another migration is currently in progress. Please wait until it completes."
                  : "Refresh delta"
              }
            >
              <Box
                color={isRefreshButtonDisabled ? "gray.400" : "inherit"}
                opacity={isRefreshButtonDisabled ? 0.5 : 1}
                p={1}
                onClick={(e) => {
                  if (isRefreshButtonDisabled) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                  }
                  onRefresh();
                }}
                cursor={isRefreshButtonDisabled ? "not-allowed" : "pointer"}
                _hover={{
                  color: isRefreshButtonDisabled ? "gray.400" : "brand.500",
                }}
              >
                {isThisTableRefreshing ? (
                  <Image
                    src={SandtimeIcon}
                    boxSize="16px"
                    objectFit="contain"
                  />
                ) : (
                  <SlRefresh />
                )}
              </Box>
            </Tooltip>
          </Flex>

          <Flex justifyContent="center" minW="40px">
            <Tooltip
              content={
                isReloadButtonDisabled
                  ? "Another migration is currently in progress. Please wait until it completes."
                  : "Reload table"
              }
            >
              <Box
                color={isReloadButtonDisabled ? "gray.400" : "inherit"}
                opacity={isReloadButtonDisabled ? 0.5 : 1}
                filter={isReloadButtonDisabled ? "blur(0.3px)" : "none"}
                _hover={{
                  color: isReloadButtonDisabled ? "gray.400" : "brand.500",
                  cursor: isReloadButtonDisabled ? "not-allowed" : "pointer",
                }}
                p={1}
                onClick={(e) => {
                  if (isReloadButtonDisabled) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                  }
                  onReload();
                }}
                style={{
                  cursor: isReloadButtonDisabled ? "not-allowed" : "pointer",
                }}
              >
                {isThisTableReloading ? (
                  <Image
                    src={SandtimeIcon}
                    boxSize="16px"
                    objectFit="contain"
                  />
                ) : (
                  <GrRefresh />
                )}
              </Box>
            </Tooltip>
          </Flex>

          <Flex justifyContent="center" minW="40px">
            <Tooltip
              content={
                selectionLocked
                  ? `Assigned to batch "${batchName}" — remove from batch to deselect`
                  : undefined
              }
              disabled={!selectionLocked}
            >
              <Box>
                <Checkbox.Root
                  colorPalette="brand"
                  variant="solid"
                  disabled={selectionLocked}
                  onCheckedChange={({ checked }) =>
                    onCheckedChange(checked === true)
                  }
                  checked={isSelected || selectionLocked}
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control
                    cursor={selectionLocked ? "not-allowed" : "pointer"}
                  />
                </Checkbox.Root>
              </Box>
            </Tooltip>
          </Flex>
        </Flex>
      </Grid>
    </Flex>
  );
};

const BatchSchema = () => {
  const context = useOutletContext<Connector>();
  const [shouldShowDisabledState, setShouldShowDisabledState] = useState(false);
  const { disable_update_schema } = context;

  const { data: allTableData, isLoading: isAllTableListLoading } =
    useFetchConnectorTableById(context.connection_id);

  const AllTableList = allTableData?.tables;
  const itemsPerPage = allTableData?.pagination_limit;

  const { data: batchesData } = useFetchBatches(context.connection_id);

  const tableToBatchName = useMemo<Map<string, string>>(() => {
    const map = new Map<string, string>();
    batchesData?.batches?.forEach((b) => {
      b.tables?.forEach((t) => {
        const name = t?.table_name;
        if (typeof name === "string" && name.length > 0) {
          map.set(name.toLowerCase(), b.name);
        }
      });
    });
    return map;
  }, [batchesData]);

  const { status: schemaStatus } = useUpdateSchemaStatus(
    context.connection_id,
    true,
  );
  const isCheckingSchemaStatus = !!schemaStatus?.is_in_progress;

  const prevIsCheckingRef = useRef(false);
  useEffect(() => {
    if (isCheckingSchemaStatus && !prevIsCheckingRef.current) {
      prevIsCheckingRef.current = true;
    } else if (!isCheckingSchemaStatus) {
      prevIsCheckingRef.current = false;
    }
  }, [isCheckingSchemaStatus]);

  useEffect(() => {
    if (
      schemaStatus &&
      !schemaStatus.is_in_progress &&
      isCheckingSchemaStatus
    ) {
      setTimeout(() => {}, 0);
      queryClient.refetchQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
    }
  }, [schemaStatus, isCheckingSchemaStatus, context.connection_id]);

  const { mutate: updateTables, isPending: isAssigningTables } =
    useUpdateSelectedTables({
      connectorId: context.connection_id,
    });

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState<string>("");

  const reloadingTables = useMemo(
    () => context.reloadingTables ?? [],
    [context.reloadingTables],
  );
  const setReloadingTables = useMemo(
    () => context.setReloadingTables ?? (() => {}),
    [context.setReloadingTables],
  );
  const [refreshingTables, setRefreshingTables] = useState<string[]>([]);
  const reloadTimestamps = useRef<Record<string, number>>({});
  const refreshTimestamps = useRef<Record<string, number>>({});

  useEffect(() => {
    reloadingTables.forEach((table: string) => {
      if (!reloadTimestamps.current[table]) {
        reloadTimestamps.current[table] = 0;
      }
    });
  }, [reloadingTables]);

  useEffect(() => {
    refreshingTables.forEach((table: string) => {
      if (!refreshTimestamps.current[table]) {
        refreshTimestamps.current[table] = 0;
      }
    });
  }, [refreshingTables]);

  const filteredTables = useMemo(() => {
    const filtered =
      AllTableList?.filter((item: ConnectorTable) =>
        item.table.toLowerCase().includes(searchQuery),
      ) || [];
    return filtered;
  }, [AllTableList, searchQuery]);

  const {
    currentData: paginatedTables,
    currentPage,
    totalPages,
    jumpToPage,
  } = usePagination<ConnectorTable>({
    data: filteredTables,
    itemsPerPage,
  });

  const { mutate: reloadSingleTable, isPending: isReloadingSingleTable } =
    useReloadSingleTable({ connectionId: context.connection_id });

  const { mutate: refreshDeltaTable } = useRefreshDeltaTable({
    connectionId: context.connection_id,
  });

  const isRefreshDeltaTableInProgress = useIsMutating({
    mutationKey: ["refreshDeltaTable", context.connection_id],
  });

  const isRefreshSchemaInProgress = useIsMutating({
    mutationKey: ["refreshSchema", context.connection_id],
  });

  const { data: tableStatusData } = useFetchTableStatus(
    context.connection_id,
    true,
  );

  const hasAnyTableInProgress = useMemo(() => {
    const hasTableInProgress =
      tableStatusData?.tables?.some(
        (table) => table.status === "in_progress",
      ) ?? false;
    const isSchemaRefreshing = isRefreshSchemaInProgress > 0;
    const isReloading = isReloadingSingleTable || reloadingTables.length > 0;
    const isRefreshing = refreshingTables.length > 0;
    return (
      hasTableInProgress || isSchemaRefreshing || isReloading || isRefreshing
    );
  }, [
    tableStatusData,
    isRefreshSchemaInProgress,
    isReloadingSingleTable,
    reloadingTables,
    refreshingTables,
  ]);

  const activeMigrations =
    tableStatusData?.tables
      ?.filter((t: { status?: string | null }) => t.status === "in_progress")
      .map((t: { table: string }) => t.table.toLowerCase()) || [];
  const activeReloads = reloadingTables.map((t) => t.toLowerCase());

  const activeRefreshes = activeMigrations.filter(
    (t) => !activeReloads.includes(t),
  );

  const isAnyRefreshing =
    isRefreshDeltaTableInProgress > 0 ||
    activeRefreshes.length > 0 ||
    refreshingTables.length > 0;
  const isSchemaSyncing = isRefreshSchemaInProgress > 0;
  const isReloadingActive =
    isReloadingSingleTable || reloadingTables.length > 0;

  const shouldLockAllReloads = isAnyRefreshing || isSchemaSyncing;
  const shouldLockAllRefreshes = isReloadingActive || isSchemaSyncing;

  useEffect(() => {
    if (hasAnyTableInProgress || isReloadingSingleTable || isAnyRefreshing) {
      setTimeout(() => {
        setShouldShowDisabledState(true);
      }, 0);
    } else if (
      reloadingTables.length === 0 &&
      refreshingTables.length === 0 &&
      !hasAnyTableInProgress
    ) {
      setTimeout(() => {
        setShouldShowDisabledState(false);
      }, 0);
    }
  }, [
    hasAnyTableInProgress,
    isReloadingSingleTable,
    reloadingTables,
    refreshingTables,
    isAnyRefreshing,
  ]);

  const hasSeenInProgressRef = useRef<Record<string, boolean>>({});
  const refreshHasSeenInProgressRef = useRef<Record<string, boolean>>({});

  // Clean up completed reloads
  useEffect(() => {
    if (isReloadingSingleTable) return;
    if (reloadingTables.length === 0 || !tableStatusData?.tables) return;

    const tablesToRemove = reloadingTables.filter((table: string) => {
      const statusItem = tableStatusData.tables.find(
        (t: { table: string; status?: string | null }) =>
          t.table.toLowerCase() === table.toLowerCase(),
      );
      const startTime = reloadTimestamps.current[table];
      const isTimeSafe = !startTime || startTime === 0;

      const status = statusItem?.status;
      if (status === "in_progress") {
        hasSeenInProgressRef.current[table] = true;
        return false;
      }

      const hasSeenInProgress = hasSeenInProgressRef.current[table];
      const isFinished =
        status === "completed" || status === "failed" || status === null;

      if (statusItem && isFinished && (hasSeenInProgress || isTimeSafe)) {
        return true;
      }

      if (!startTime) {
        return true;
      }

      return false;
    });

    if (tablesToRemove.length > 0) {
      setReloadingTables((prev: string[]) =>
        prev.filter((t: string) => !tablesToRemove.includes(t)),
      );

      tablesToRemove.forEach((t) => {
        delete reloadTimestamps.current[t];
        delete hasSeenInProgressRef.current[t];
      });

      queryClient.refetchQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
      queryClient.refetchQueries({
        queryKey: ["TableStatus", context.connection_id],
      });
    }
  }, [
    tableStatusData,
    reloadingTables,
    setReloadingTables,
    context.connection_id,
    isReloadingSingleTable,
  ]);

  useEffect(() => {
    if (refreshingTables.length === 0 || !tableStatusData?.tables) return;

    const tablesToRemove = refreshingTables.filter((table: string) => {
      const statusItem = tableStatusData.tables.find(
        (t: { table: string; status?: string | null }) =>
          t.table.toLowerCase() === table.toLowerCase(),
      );
      const startTime = refreshTimestamps.current[table];
      const isTimeSafe = !startTime || startTime === 0;

      const status = statusItem?.status;
      if (status === "in_progress") {
        refreshHasSeenInProgressRef.current[table] = true;
        return false;
      }

      const hasSeenInProgress = refreshHasSeenInProgressRef.current[table];
      const isFinished =
        status === "completed" || status === "failed" || status === null;

      if (statusItem && isFinished && (hasSeenInProgress || isTimeSafe)) {
        return true;
      }

      if (!startTime) {
        return true;
      }

      return false;
    });

    if (tablesToRemove.length > 0) {
      setRefreshingTables((prev: string[]) =>
        prev.filter((t: string) => !tablesToRemove.includes(t)),
      );

      tablesToRemove.forEach((t) => {
        delete refreshTimestamps.current[t];
        delete refreshHasSeenInProgressRef.current[t];
      });

      queryClient.refetchQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
      queryClient.refetchQueries({
        queryKey: ["TableStatus", context.connection_id],
      });
    }
  }, [tableStatusData, refreshingTables, context.connection_id]);

  const checkedTables = useMemo<ConnectorTable[]>(() => {
    if (!AllTableList) return [];
    const byKey = new Map<string, ConnectorTable>();
    for (const t of AllTableList) {
      if (isConnectorTableMarkedSelected(t.selected)) {
        byKey.set(t.table.toLowerCase(), t);
      }
    }
    // Batched tables count as selected even if the API selected flag is still false.
    for (const t of AllTableList) {
      const key = t.table.toLowerCase();
      if (tableToBatchName.has(key) && !byKey.has(key)) {
        byKey.set(key, t);
      }
    }
    return Array.from(byKey.values()).sort(
      (a: ConnectorTable, b: ConnectorTable) => {
        const seqA = a.sequence ?? 0;
        const seqB = b.sequence ?? 0;
        return seqA - seqB;
      },
    );
  }, [AllTableList, tableToBatchName]);

  const [copyOfInitialCheckedTables, setCopyOfInitialCheckedTables] = useState<
    ConnectorTable[]
  >([]);
  const [userCheckedTables, setUserCheckedTables] = useState<ConnectorTable[]>(
    () => checkedTables,
  );

  const shouldSkipUpdateRef = useRef(false);
  /**
   * After any checkbox toggle, only `userCheckedTables` drives UI + Migration Batches until Save.
   * Before that, server `checkedTables` drives both so the right panel matches purple checks from API `selected`.
   */
  const [hasLocalSelectionEdits, setHasLocalSelectionEdits] = useState(false);
  /** First checkbox change in this visit: merge from server `checkedTables`; after that use previous `userCheckedTables`. */
  const beganLocalSelectionRef = useRef(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setHasLocalSelectionEdits(false);
      beganLocalSelectionRef.current = false;
    }, 0);
    return () => window.clearTimeout(timer);
  }, [context.connection_id]);

  useEffect(() => {
    if (shouldSkipUpdateRef.current) return;
    if (hasLocalSelectionEdits) return;
    const id = window.setTimeout(() => {
      setCopyOfInitialCheckedTables(checkedTables);
      setUserCheckedTables(checkedTables);
    }, 0);
    return () => window.clearTimeout(id);
  }, [
    checkedTables,
    disable_update_schema,
    AllTableList,
    hasLocalSelectionEdits,
  ]);

  /** Rows treated as selected for checkboxes + Unassigned column (server truth OR local edits). */
  const effectiveSelectedRows = useMemo<ConnectorTable[]>(() => {
    return hasLocalSelectionEdits ? userCheckedTables : checkedTables;
  }, [hasLocalSelectionEdits, userCheckedTables, checkedTables]);

  const effectiveSelectedKeys = useMemo(() => {
    return new Set(effectiveSelectedRows.map((t) => t.table.toLowerCase()));
  }, [effectiveSelectedRows]);

  const hasCheckedTablesChanged = useMemo(() => {
    return (
      userCheckedTables.length !== copyOfInitialCheckedTables.length ||
      userCheckedTables.some(
        (table: ConnectorTable) =>
          !copyOfInitialCheckedTables.find(
            (t: ConnectorTable) => t.table === table.table,
          ),
      )
    );
  }, [userCheckedTables, copyOfInitialCheckedTables]);

  /**
   * Checked on the left but not assigned to a named batch.
   * mergeUnassigned() dedupes with API unassigned rows; do not exclude API names here
   * (that hid rows when GET /batches lagged, failed, or casing differed).
   */
  const pendingUnassignedTables = useMemo<UnassignedTable[]>(() => {
    return effectiveSelectedRows
      .filter((t) => !tableToBatchName.has(t.table.toLowerCase()))
      .map((t) => ({
        table_name: t.table,
        sequence: t.sequence ?? 0,
        last_synced: t.last_synced ?? null,
      }));
  }, [effectiveSelectedRows, tableToBatchName]);

  const toggleExpand = (table: string) =>
    setExpanded((prev) => ({
      ...prev,
      [table]: !prev[table],
    }));

  const handleAssignTables = () => {
    const tablesToAdd = userCheckedTables.map((t) => t.table);
    const savedTables = [...userCheckedTables];
    updateTables(
      { selected_tables: tablesToAdd },
      {
        onSuccess: async (response) => {
          const message =
            response?.data?.message || "Tables updated successfully";
          const warning = response?.data?.warning;

          if (warning) {
            toaster.warning({
              title: warning,
            });
          } else {
            toaster.success({
              title: message,
            });
          }

          shouldSkipUpdateRef.current = true;
          setCopyOfInitialCheckedTables(savedTables);
          setUserCheckedTables(savedTables);
          setHasLocalSelectionEdits(false);
          beganLocalSelectionRef.current = false;
          shouldSkipUpdateRef.current = false;
        },
      },
    );
  };

  if (isAllTableListLoading) {
    return <LoadingSpinner />;
  }

  return (
    <Flex flexDirection="column" gap={4} pb={8} w="100%">
      <Actions
        shouldShowDisabledState={shouldShowDisabledState}
        setShouldShowDisabledState={setShouldShowDisabledState}
        reloadingTables={reloadingTables}
        onUpdateSchemaComplete={() => {
          queryClient.refetchQueries({
            queryKey: ["ConnectorTable", context.connection_id],
          });
        }}
      />
      <Flex direction="column" gap={1} mr="auto" mt={-4} w="100%" maxW="md">
        <InputGroup endElement={<MdSearch size={28} />}>
          <Input
            placeholder="Search table name"
            size="md"
            onChange={(e) => {
              const query = e.target.value.toLowerCase();
              setSearchQuery(query);
              jumpToPage(1);
            }}
          />
        </InputGroup>
        {hasLocalSelectionEdits && (
          <Text fontSize="xs" color="gray.600">
            Use the Save bar to write your checked tables to the server, then
            add them to a batch.
          </Text>
        )}
      </Flex>
      <Grid templateColumns="1fr 1fr" gap={4} alignItems="stretch">
        <Flex
          direction="column"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          padding={4}
          bgColor="white"
          minH={0}
          maxH="72vh"
          overflow="hidden"
        >
          <Flex
            mb={3}
            justifyContent="space-between"
            alignItems="center"
            flexShrink={0}
          >
            <Text fontSize="sm" fontWeight="semibold">
              Table Names
            </Text>
            <Flex gap={6} alignItems="center">
              <Text fontSize="sm" fontWeight="semibold" minW="40px">
                Delta
              </Text>
              <Text fontSize="sm" fontWeight="semibold" minW="40px">
                Refresh
              </Text>
              <Text fontSize="sm" fontWeight="semibold" minW="40px">
                Reload
              </Text>
              <Text fontSize="sm" fontWeight="semibold" minW="40px">
                Select
              </Text>
            </Flex>
          </Flex>

          {(isAssigningTables || isAllTableListLoading) && (
            <For each={[...Array(10).keys()]}>
              {(_item, index) => <Skeleton key={index} gap="4" height={8} />}
            </For>
          )}

          {!AllTableList?.length && !isAllTableListLoading && (
            <Flex direction="column" alignItems="center">
              <Text>No Tables available</Text>
            </Flex>
          )}

          {!isAssigningTables && (
            <>
              <Flex
                direction="column"
                gap={2}
                flex="1"
                minH={0}
                overflowY="auto"
                pr={1}
              >
                {paginatedTables.map((item, index) => {
                  const { table } = item;
                  const isExpanded = !!expanded[table];
                  const batchName = tableToBatchName.get(table.toLowerCase());

                  return (
                    <TableRow
                      key={table}
                      item={item}
                      index={index}
                      connectionId={context.connection_id}
                      isExpanded={isExpanded}
                      onToggleExpand={toggleExpand}
                      isSelected={effectiveSelectedKeys.has(
                        table.toLowerCase(),
                      )}
                      selectionLocked={!!batchName}
                      onCheckedChange={(checked) => {
                        setHasLocalSelectionEdits(true);
                        setUserCheckedTables((prev: ConnectorTable[]) => {
                          const base = beganLocalSelectionRef.current
                            ? prev
                            : checkedTables;
                          beganLocalSelectionRef.current = true;
                          if (checked) {
                            if (base.some((t) => t.table === table))
                              return base;
                            return [...base, item];
                          }
                          return base.filter(
                            (t: ConnectorTable) => t.table !== table,
                          );
                        });
                      }}
                      reloadingTables={reloadingTables}
                      refreshingTables={refreshingTables}
                      shouldLockAllReloads={shouldLockAllReloads}
                      shouldLockAllRefreshes={shouldLockAllRefreshes}
                      tableStatusData={tableStatusData}
                      batchName={batchName}
                      onRefresh={() => {
                        setShouldShowDisabledState(true);
                        delete refreshHasSeenInProgressRef.current[table];
                        setRefreshingTables((prev: string[]) => [
                          ...prev,
                          table,
                        ]);
                        refreshTimestamps.current[table] = Date.now();
                        refreshDeltaTable(
                          {
                            connection_id: context.connection_id,
                            table_name: table,
                          },
                          {
                            onError: () => {
                              setRefreshingTables((prev: string[]) =>
                                prev.filter((t: string) => t !== table),
                              );
                              setShouldShowDisabledState(false);
                            },
                          },
                        );
                      }}
                      onReload={() => {
                        setShouldShowDisabledState(true);
                        setReloadingTables((prev: string[]) => [
                          ...prev,
                          table,
                        ]);
                        reloadTimestamps.current[table] = Date.now();
                        reloadSingleTable({
                          connection_id: context.connection_id,
                          table_name: table,
                        });
                      }}
                    />
                  );
                })}
              </Flex>
              {totalPages > 1 && (
                <Flex
                  flexShrink={0}
                  pt={3}
                  borderTopWidth={1}
                  borderColor="gray.200"
                >
                  <Pagination
                    currentPage={currentPage}
                    totalPages={totalPages}
                    onPageChange={jumpToPage}
                  />
                </Flex>
              )}
            </>
          )}
        </Flex>

        <BatchGroupedPanel
          connectionId={context.connection_id}
          pendingUnassignedTables={pendingUnassignedTables}
        />
      </Grid>

      <ActionBar.Root open={hasCheckedTablesChanged}>
        <Portal>
          <ActionBar.Positioner>
            <ActionBar.Content>
              <Button
                variant="solid"
                colorPalette="purple"
                size="sm"
                onClick={() => handleAssignTables()}
                loading={isAssigningTables}
              >
                <GoPlus />
                Save
              </Button>
            </ActionBar.Content>
          </ActionBar.Positioner>
        </Portal>
      </ActionBar.Root>
    </Flex>
  );
};

const Schema = () => {
  const context = useOutletContext<Connector>();

  if (
    context.source_name?.toLowerCase().replace(/[\s\-.]/g, "") === "sapodata"
  ) {
    return <SAPODataSchema />;
  }

  return <BatchSchema />;
};

export default Schema;
