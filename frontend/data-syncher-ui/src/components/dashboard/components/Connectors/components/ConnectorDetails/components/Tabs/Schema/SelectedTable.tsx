import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Box, Flex, For, Image, Skeleton, Text } from "@chakra-ui/react";

import { RxDragHandleDots2 } from "react-icons/rx";
import { SlRefresh } from "react-icons/sl";

import { useOutletContext } from "react-router";

import CheckIcon from "@/assets/icons/check-icon.svg";
import ErrorIcon from "@/assets/icons/error-icon.svg";
import SandtimeIcon from "@/assets/icons/sand-time-icon.svg";
import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import useRefreshDeltaTable from "@/queryOptions/connector/schema/useRefreshDeltaTable";
import useUpdateSelectedTables from "@/queryOptions/connector/schema/useUpdateSelectedTables";
import {
  type Connector,
  type ConnectorSelectedTable,
  type ConnectorTable,
} from "@/types/connectors";

const SelectedTable = ({
  selectedTablesFromMain,
  reloadingTables,
  isReloadingSingleTable,
  shouldLockAllReloads,
  shouldLockAllRefreshes,
  refreshingTables,
  setRefreshingTables,
}: {
  selectedTablesFromMain: ConnectorTable[];
  reloadingTables: string[];
  isReloadingSingleTable: boolean;
  shouldLockAllReloads: boolean;
  shouldLockAllRefreshes: boolean;
  refreshingTables: string[];
  setRefreshingTables: React.Dispatch<React.SetStateAction<string[]>>;
}) => {
  const context = useOutletContext<Connector>();

  const refreshTimestamps = useRef<Record<string, number>>({});
  const hasSeenInProgressRef = useRef<Record<string, boolean>>({});

  const { mutate: updateTables, isPending: isAssigningTables } =
    useUpdateSelectedTables({
      connectorId: context.connection_id,
    });

  const { mutate: refreshDeltaTable } = useRefreshDeltaTable({
    connectionId: context.connection_id,
  });

  const { data: tableStatusData } = useFetchTableStatus(
    context.connection_id,
    true,
  );

  const [lastKnownStatus, setLastKnownStatus] = useState<
    Record<string, string>
  >({});

  useEffect(() => {
    if (tableStatusData?.tables) {
      const timer = setTimeout(() => {
        setLastKnownStatus((prev) => {
          let hasChanges = false;
          const next = { ...prev };
          tableStatusData.tables.forEach((t) => {
            if (t.status === "completed" && prev[t.table] !== "completed") {
              next[t.table] = "completed";
              hasChanges = true;
            }
          });
          return hasChanges ? next : prev;
        });
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [tableStatusData]);

  useEffect(() => {
    refreshingTables.forEach((table) => {
      if (!refreshTimestamps.current[table]) {
        refreshTimestamps.current[table] = 0;
      }
    });
  }, [refreshingTables]);

  useEffect(() => {
    if (refreshingTables.length === 0 || !tableStatusData?.tables) return;

    const tablesToRemove = refreshingTables.filter((tName) => {
      const statusItem = tableStatusData.tables.find(
        (t) => t.table.toLowerCase() === tName.toLowerCase(),
      );
      const startTime = refreshTimestamps.current[tName];
      const isTimeSafe = !startTime || startTime === 0;

      const status = statusItem?.status;
      if (status === "in_progress") {
        hasSeenInProgressRef.current[tName] = true;
        return false;
      }

      const hasSeenInProgress = hasSeenInProgressRef.current[tName];
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
      setRefreshingTables((prev) =>
        prev.filter((t) => !tablesToRemove.includes(t)),
      );
      tablesToRemove.forEach((t) => {
        delete refreshTimestamps.current[t];
        delete hasSeenInProgressRef.current[t];
      });
    }
  }, [tableStatusData, refreshingTables, setRefreshingTables]);

  const selectedTables = useMemo(() => {
    if (!selectedTablesFromMain || selectedTablesFromMain.length === 0) {
      return [];
    }

    if (!tableStatusData?.tables) {
      return selectedTablesFromMain.map((table) => ({
        tbl_id: 0,
        table: table.table,
        sequence: table.sequence || 0,
        status: null,
      }));
    }

    return selectedTablesFromMain.map((table: ConnectorTable) => {
      const statusFromAPI = tableStatusData.tables.find(
        (t) => t.table === table.table,
      );

      const status = statusFromAPI?.status;
      const validStatus =
        status === "in_progress" ||
        status === "completed" ||
        status === "failed"
          ? status
          : null;

      return {
        tbl_id: 0,
        table: table.table,
        sequence: table.sequence || 0,
        status: validStatus,
      };
    });
  }, [selectedTablesFromMain, tableStatusData]);

  const getTableStatus = useCallback(
    (tableName: string) => {
      return tableStatusData?.tables?.find((t) => t.table === tableName)
        ?.status;
    },
    [tableStatusData],
  );

  const [draggedItem, setDraggedItem] = useState<null | ConnectorSelectedTable>(
    null,
  );
  const handleDragStart = (table: ConnectorSelectedTable) => {
    setDraggedItem(table);
  };
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };
  const handleDrop = (targetItem: ConnectorSelectedTable) => {
    if (!draggedItem || draggedItem.table === targetItem.table) return;

    const newList: ConnectorSelectedTable[] = [...(selectedTables || [])];
    const draggedIndex = newList.findIndex(
      (i) => i.table === draggedItem.table,
    );
    const targetIndex = newList.findIndex((i) => i.table === targetItem.table);

    const draggedItemToInsert = {
      ...draggedItem,
      status: draggedItem.status ?? null,
    };

    newList.splice(draggedIndex, 1);
    newList.splice(targetIndex, 0, draggedItemToInsert);

    const tablesToUpdate = newList.map((t) => t.table);
    updateTables(
      { selected_tables: tablesToUpdate },
      {
        onSuccess: () => {
          toaster.success({ title: "Table order updated successfully" });
        },
      },
    );
    setDraggedItem(null);
  };

  return (
    <Flex
      direction="column"
      gap={2}
      borderWidth={1}
      borderColor="gray.300"
      borderRadius="lg"
      padding={4}
      bgColor="white"
    >
      <Flex mb={4} justifyContent="space-between" alignItems="center">
        <Text fontSize="sm" fontWeight="semibold">
          Selected Tables
        </Text>
        <Flex gap={3} alignItems="center">
          <Text
            fontSize="sm"
            fontWeight="semibold"
            textAlign="center"
            minW="40px"
          >
            Status
          </Text>
          <Text
            fontSize="sm"
            fontWeight="semibold"
            textAlign="center"
            minW="40px"
          >
            Refresh
          </Text>
          <Box minW="40px" />
        </Flex>
      </Flex>
      {isAssigningTables && (
        <For each={[...Array(10).keys()]}>
          {(_, index) => <Skeleton gap="4" height={8} key={index} />}
        </For>
      )}
      {!selectedTables?.length && !isAssigningTables && (
        <Flex direction="column" alignItems="center">
          <Text>No Tables available</Text>
        </Flex>
      )}
      {!isAssigningTables &&
        selectedTables?.map((table: ConnectorSelectedTable, index: number) => {
          const isEven = index % 2 === 0;
          const rowBg = isEven ? "gray.100" : "white";
          const { status } = table;

          const isThisTableRefreshing = refreshingTables.includes(table.table);

          const isThisTableProcessing =
            isThisTableRefreshing ||
            getTableStatus(table.table) === "in_progress";

          const isThisTableReloading =
            reloadingTables?.some(
              (t) => t.toLowerCase() === table.table.toLowerCase(),
            ) ?? false;

          const isReloadDisabled =
            shouldLockAllReloads ||
            isThisTableReloading ||
            isThisTableProcessing ||
            isReloadingSingleTable;

          const isRefreshDisabled =
            shouldLockAllRefreshes ||
            isThisTableReloading ||
            isThisTableProcessing;

          const isLocked = isReloadDisabled || isRefreshDisabled;

          return (
            <Flex
              key={table.table}
              justifyContent="space-between"
              backgroundColor={rowBg}
              alignItems="center"
              padding={2}
              borderRadius={4}
              draggable={!isLocked}
              onDragStart={() => !isLocked && handleDragStart(table)}
              onDragOver={handleDragOver}
              onDrop={() => !isLocked && handleDrop(table)}
            >
              <Flex gap={2} alignItems="center" flex="1">
                <Text fontSize="sm">{table.table}</Text>
              </Flex>
              <Flex gap={3} alignItems="center">
                <Flex justifyContent="center" minW="40px">
                  {(() => {
                    // Logic to persist "Green Tick" (completed) during reload
                    const savedStatus = lastKnownStatus[table.table];
                    const effectiveStatus =
                      isThisTableReloading && savedStatus === "completed"
                        ? "completed"
                        : status;

                    const shouldShow = !(
                      isThisTableReloading && effectiveStatus !== "completed"
                    );

                    if (!shouldShow) return null;

                    return (
                      <>
                        {effectiveStatus === "in_progress" && (
                          <Image src={SandtimeIcon} />
                        )}
                        {effectiveStatus === "completed" && (
                          <Image src={CheckIcon} />
                        )}
                        {effectiveStatus === "failed" && (
                          <Image src={ErrorIcon} />
                        )}
                      </>
                    );
                  })()}
                </Flex>
                <Flex justifyContent="center" minW="40px">
                  <Tooltip
                    content={
                      isRefreshDisabled
                        ? "Another migration is currently in progress. Please wait until it completes."
                        : ""
                    }
                    disabled={!isRefreshDisabled}
                  >
                    <Box
                      color={isRefreshDisabled ? "gray.400" : "inherit"}
                      opacity={isRefreshDisabled ? 0.5 : 1}
                      filter={isRefreshDisabled ? "blur(0.3px)" : "none"}
                      _hover={{
                        color: isRefreshDisabled ? "gray.400" : "brand.500",
                        cursor: isRefreshDisabled ? "not-allowed" : "pointer",
                      }}
                      p={1}
                      borderRadius="sm"
                      onClick={() => {
                        if (isRefreshDisabled) {
                          toaster.warning({
                            title: "Operation in progress",
                            description:
                              "Another migration is currently in progress. Please wait until it completes.",
                          });
                          return;
                        }

                        delete hasSeenInProgressRef.current[table.table];

                        setRefreshingTables((prev) => [...prev, table.table]);
                        refreshTimestamps.current[table.table] = Date.now();
                        refreshDeltaTable(
                          {
                            connection_id: context.connection_id,
                            table_name: table.table,
                          },
                          {
                            onSuccess: () => {},
                            onError: () => {
                              setRefreshingTables((prev) =>
                                prev.filter((t) => t !== table.table),
                              );
                            },
                          },
                        );
                      }}
                      style={{
                        cursor: isRefreshDisabled ? "not-allowed" : "pointer",
                      }}
                    >
                      <SlRefresh
                        className={
                          isThisTableProcessing && !isThisTableReloading
                            ? "spin-animation"
                            : ""
                        }
                      />
                    </Box>
                  </Tooltip>
                </Flex>
                <Flex justifyContent="center" minW="40px">
                  <Box
                    _hover={{
                      color: isLocked ? "gray.400" : "brand.500",
                      backgroundColor: isLocked ? "transparent" : "gray.300",
                    }}
                    p={1}
                    borderRadius="sm"
                    cursor={isLocked ? "not-allowed" : "grab"}
                    color={isLocked ? "gray.300" : "inherit"}
                  >
                    <RxDragHandleDots2 />
                  </Box>
                </Flex>
              </Flex>
            </Flex>
          );
        })}
    </Flex>
  );
};

export default SelectedTable;
