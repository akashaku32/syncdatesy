import { useEffect, useMemo, useState } from "react";

import { Box, Flex, Grid, NativeSelect, Text } from "@chakra-ui/react";

import { useOutletContext } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import {
  isTransientMigrationMessage,
  patchActivityLogForMigration,
  resolveJobLevelBannerMessage,
} from "@/helpers/activityLog";
import useFetchConnectorActivity from "@/queryOptions/connector/useFetchConnectorActivity";
import useFetchConnectorActivityDetails from "@/queryOptions/connector/useFetchConnectorActivityDetails";
import { type Connector } from "@/types/connectors";

import Filter from "./Filter";
import Item from "./Item";
import MigrationStatusWSListener from "./MigrationStatusWSListener";
import MigrationProgressTable from "./components/MigrationProgressTable";
import TableSelectionDetails from "./components/TableSelectionDetails";
import { useQueryClient } from "@tanstack/react-query";

const Overview = () => {
  const queryClient = useQueryClient();
  const context = useOutletContext<
    Connector & { filterDays: number; setFilterDays: (_days: number) => void }
  >();

  const [statusFilter, setStatusFilter] = useState("all");

  const { data, isLoading } = useFetchConnectorActivity(
    context.connection_id,
    context.filterDays,
    statusFilter,
  );

  const [userSelectedLog, setUserSelectedLog] = useState<number | null>(null);

  // Derive the default selected log from data (first clickable log in the list)
  const defaultSelectedLog = useMemo(() => {
    if (data?.logs?.length) {
      const firstLog = data.logs[0];
      if (firstLog.is_clickable !== false) {
        return Number(
          firstLog.log_id ?? firstLog.migration_id ?? firstLog.session_id ?? 0,
        );
      }
    }
    return null;
  }, [data]);

  // Effective selected log: user selection wins, otherwise use default
  const effectiveSelectedLog = userSelectedLog ?? defaultSelectedLog;

  // Find the active log object to check properties
  const activeLog = useMemo(
    () =>
      data?.logs?.find(
        (log) =>
          (log.log_id && log.log_id === effectiveSelectedLog) ||
          log.migration_id === effectiveSelectedLog ||
          log.session_id === effectiveSelectedLog,
      ),
    [data, effectiveSelectedLog],
  );

  // Only fetch if is_clickable is true
  const migrationIdToFetch =
    activeLog?.is_clickable && activeLog?.migration_id
      ? activeLog.migration_id
      : undefined;

  const logIdToFetch =
    activeLog?.is_clickable && !activeLog?.migration_id && activeLog?.log_id
      ? activeLog.log_id
      : undefined;

  const migrationIdsForWS = useMemo(() => {
    const ids = new Set<number>();
    for (const log of data?.logs ?? []) {
      if (log.is_clickable === false || !log.migration_id) continue;
      const status = (log.status || "").toUpperCase();
      if (status === "I" || isTransientMigrationMessage(log.message)) {
        ids.add(Number(log.migration_id));
      }
    }
    if (migrationIdToFetch) {
      ids.add(Number(migrationIdToFetch));
    }
    return [...ids];
  }, [data?.logs, migrationIdToFetch]);

  const { data: logDetails, isLoading: isLoadingDetails } =
    useFetchConnectorActivityDetails({
      migrationId: migrationIdToFetch,
      // Always scope details to this connector — session IDs can collide across connections.
      connectionId: migrationIdToFetch
        ? context.connection_id
        : logIdToFetch
          ? context.connection_id
          : undefined,
      logId: logIdToFetch,
    });

  // Reset selected log when switching connectors to avoid stale details.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset selection when connection changes
    setUserSelectedLog(null);
  }, [context.connection_id]);

  useEffect(() => {
    if (!migrationIdToFetch || !logDetails?.overall_status) return;
    const status = logDetails.overall_status.toLowerCase();
    if (
      !status.includes("completed") &&
      !status.includes("success") &&
      !status.includes("failed") &&
      !status.includes("error")
    ) {
      return;
    }
    const jobLevelMessage = logDetails.job_level_message || undefined;
    patchActivityLogForMigration(
      queryClient,
      context.connection_id,
      migrationIdToFetch,
      {
        overallStatus: status,
        message:
          jobLevelMessage && !isTransientMigrationMessage(jobLevelMessage)
            ? jobLevelMessage
            : undefined,
      },
    );
  }, [
    context.connection_id,
    logDetails?.job_level_message,
    logDetails?.overall_status,
    migrationIdToFetch,
    queryClient,
  ]);

  const displayJobLevelMessage = useMemo(
    () =>
      resolveJobLevelBannerMessage(
        logDetails?.overall_status,
        logDetails?.job_level_message,
      ),
    [logDetails?.job_level_message, logDetails?.overall_status],
  );

  if (isLoading) return <LoadingSpinner />;

  return (
    <Flex flexDirection="column" gap={2} w="100%" h="full">
      {migrationIdsForWS.map((migrationId) => (
        <MigrationStatusWSListener
          key={migrationId}
          migrationId={migrationId}
          connectionId={context.connection_id}
        />
      ))}
      {/* Top Level Filter */}
      <Flex justifyContent="flex-end">
        <Filter
          filterDays={context.filterDays}
          setFilterDays={context.setFilterDays}
        />
      </Flex>

      <Grid
        templateColumns={{ base: "1fr", lg: "0.9fr 1.1fr" }}
        gap={6}
        flex={1}
        minH="0"
      >
        {/* Left Panel: Migration Logs */}
        <Flex
          direction="column"
          bgColor="white"
          borderRadius="lg"
          border="1px solid"
          borderColor="gray.200"
          overflow="hidden"
          h="full"
        >
          <Flex
            justifyContent="space-between"
            alignItems="center"
            p={2.5}
            borderBottom="1px solid"
            borderColor="gray.200"
          >
            <Text fontWeight="bold" fontSize="md" color="gray.700">
              Logs
            </Text>
            <NativeSelect.Root size="sm" width="130px">
              <NativeSelect.Field
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.currentTarget.value)}
              >
                <option value="all">All Status</option>
                <option value="completed">Completed</option>
                <option value="failed">Failed</option>
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
          </Flex>

          <Flex direction="column" overflowY="auto" flex={1}>
            {data?.logs?.length === 0 && (
              <Flex
                direction="column"
                alignItems="center"
                justifyContent="center"
                gap={2}
                padding={8}
                h="100%"
              >
                <Text color="gray.500">
                  {statusFilter !== "all"
                    ? `No matching logs found`
                    : "No logs available"}
                </Text>
              </Flex>
            )}
            {data?.logs?.map((log, index) => (
              <Item
                key={`${log.log_id || log.migration_id || log.session_id || "log"}-${index}`}
                log={log}
                onClick={() => {
                  if (log.is_clickable === false) return;
                  const logId =
                    log.log_id ?? log.migration_id ?? log.session_id;
                  if (logId) {
                    setUserSelectedLog(logId);
                  }
                }}
                pointerEvent={
                  log.is_clickable === false ? "not-allowed" : "pointer"
                }
                selectedLog={effectiveSelectedLog}
              />
            ))}
          </Flex>
        </Flex>

        {/* Right Panel: Migration Progress */}
        <Flex
          direction="column"
          bgColor="white"
          borderRadius="lg"
          border="1px solid"
          borderColor="gray.200"
          overflow="hidden"
          h="full"
        >
          <Flex
            justifyContent="space-between"
            alignItems="center"
            p={4}
            borderBottom="1px solid"
            borderColor="gray.200"
          >
            <Text fontWeight="bold" fontSize="md" color="gray.700">
              Migration Details
            </Text>
          </Flex>

          <Flex direction="column" flex={1} overflowY="auto" p={0}>
            {isLoadingDetails ? (
              <LoadingSpinner />
            ) : (
              <>
                {!effectiveSelectedLog && (
                  <Flex
                    direction="column"
                    alignItems="center"
                    justifyContent="center"
                    gap={2}
                    padding={8}
                    h="100%"
                  >
                    <Text color="gray.500">Select a log to view details</Text>
                  </Flex>
                )}
                {effectiveSelectedLog && activeLog && (
                  <Box p={0} h="full">
                    {displayJobLevelMessage && (
                      <Box
                        px={4}
                        py={2}
                        borderBottom="1px solid"
                        borderColor="gray.200"
                        bg={(() => {
                          const overall = (
                            logDetails?.overall_status || ""
                          ).toLowerCase();
                          if (overall.includes("failed")) return "red.50";
                          if (
                            overall.includes("completed") ||
                            overall.includes("success")
                          ) {
                            return "green.50";
                          }
                          return "orange.50";
                        })()}
                      >
                        <Text
                          fontSize="xs"
                          color={(() => {
                            const overall = (
                              logDetails?.overall_status || ""
                            ).toLowerCase();
                            if (overall.includes("failed")) return "red.700";
                            if (
                              overall.includes("completed") ||
                              overall.includes("success")
                            ) {
                              return "green.700";
                            }
                            return "orange.800";
                          })()}
                        >
                          {displayJobLevelMessage}
                        </Text>
                      </Box>
                    )}
                    {logDetails?.overall_warning_message && (
                      <Box
                        px={4}
                        py={2}
                        borderBottom="1px solid"
                        borderColor="gray.200"
                        bg="orange.50"
                      >
                        <Text fontSize="xs" color="orange.800">
                          {logDetails.overall_warning_message}
                        </Text>
                      </Box>
                    )}
                    {logDetails?.changes ? (
                      <TableSelectionDetails
                        changes={logDetails.changes || []}
                      />
                    ) : (
                      <MigrationProgressTable
                        tables={logDetails?.tables || []}
                        progressCountSource={logDetails?.progress_count_source}
                      />
                    )}
                  </Box>
                )}
              </>
            )}
          </Flex>
        </Flex>
      </Grid>
    </Flex>
  );
};

export default Overview;
