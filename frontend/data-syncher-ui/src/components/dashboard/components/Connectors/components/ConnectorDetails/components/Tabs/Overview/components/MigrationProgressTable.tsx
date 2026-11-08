import { useMemo } from "react";

import { Box, Flex, Image, Table, Text } from "@chakra-ui/react";

import { MdWarning } from "react-icons/md";

import { format } from "date-fns";
import { useOutletContext } from "react-router";

import CheckIcon from "@/assets/icons/check-icon.svg";
import ErrorIcon from "@/assets/icons/error-icon.svg";
import SandtimeIcon from "@/assets/icons/sand-time-icon.svg";
import { Tooltip } from "@/components/ui/tooltip";
import { dateTimeFormat } from "@/constants/common";
import { getUiState } from "@/helpers/log";
import {
  isTableRowFailed,
  resolveTableRowDisplayError,
} from "@/helpers/migrationTableErrors";
import useFetchConnectorTableById from "@/queryOptions/connector/schema/useFetchTable";
import {
  type Connector,
  type ConnectorActivityDetailResponse,
} from "@/types/connectors";

import { TableFailureTooltip } from "./TableFailureTooltip";

const MigrationProgressTable = ({
  tables,
  progressCountSource = "staging",
  nodeOrBatchErrorFallback,
}: {
  tables: ConnectorActivityDetailResponse["tables"];
  /** base_merged = live cumulative rows merged to destination (Databricks per-chunk). */
  progressCountSource?: ConnectorActivityDetailResponse["progress_count_source"];
  /** Pipeline/batch-level error when per-table messages are empty or generic. */
  nodeOrBatchErrorFallback?: string | null;
}) => {
  const context = useOutletContext<Connector | undefined>();
  const { data: schemaTables } = useFetchConnectorTableById(
    context?.connection_id || 0,
  );
  const destByTable = useMemo(() => {
    const map = new Map<string, string>();
    schemaTables?.tables?.forEach((t) => {
      const dest = t.transform_summary?.destination_table_name;
      if (dest) map.set(t.table.toLowerCase(), dest);
    });
    return map;
  }, [schemaTables?.tables]);

  if (!tables || tables.length === 0) {
    return (
      <Flex
        direction="column"
        alignItems="center"
        justifyContent="center"
        padding={8}
        h="full"
      >
        <Text color="gray.500">No migration details recorded yet.</Text>
      </Flex>
    );
  }

  const anyInProgress = tables.some((table) => {
    const statusRaw = (table.status_icon || table.status || "").toLowerCase();
    const uiState = getUiState(
      table.status_icon,
      table.status,
      table.message || table.error_message || "",
    );
    const normalizedState = (uiState || statusRaw).toLowerCase();
    return (
      normalizedState === "in_progress" ||
      normalizedState === "pending" ||
      normalizedState === "i" ||
      normalizedState === "running" ||
      ![
        "success",
        "completed",
        "s",
        "failed",
        "error",
        "f",
        "e",
        "warning",
        "p",
        "w",
        "skipped",
      ].includes(normalizedState)
    );
  });
  const recordsColumnHeader = anyInProgress
    ? progressCountSource === "base_merged"
      ? "Records Migrated"
      : "Records Staging"
    : "Records Migrated";

  return (
    <Box w="100%" overflowX="auto">
      <Table.Root>
        <Table.Header bg="gray.50">
          <Table.Row>
            <Table.ColumnHeader
              width="50px"
              textAlign="center"
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
            >
              Status
            </Table.ColumnHeader>
            <Table.ColumnHeader
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
            >
              Table Name
            </Table.ColumnHeader>
            <Table.ColumnHeader
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
              whiteSpace="nowrap"
              minW="200px"
            >
              Start Time
            </Table.ColumnHeader>
            <Table.ColumnHeader
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
              whiteSpace="nowrap"
              minW="200px"
            >
              End Time
            </Table.ColumnHeader>
            <Table.ColumnHeader
              fontWeight="bold"
              color="gray.600"
              textAlign="left"
              whiteSpace="nowrap"
              minW="140px"
              py={1}
            >
              {recordsColumnHeader}
            </Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {tables?.map((table, index: number) => {
            const statusRaw = (
              table.status_icon ||
              table.status ||
              ""
            ).toLowerCase();
            const uiState = getUiState(
              table.status_icon,
              table.status,
              table.message || table.error_message || "",
            );
            const normalizedState = (uiState || statusRaw).toLowerCase();
            const isSuccess = ["success", "completed", "s"].includes(
              normalizedState,
            );
            const isFailed =
              isTableRowFailed(table) ||
              ["failed", "error", "f", "e"].includes(normalizedState);
            const isWarning = ["warning", "p", "w"].includes(normalizedState);
            const isSkipped = ["skipped"].includes(normalizedState);
            const isPending =
              normalizedState === "in_progress" ||
              normalizedState === "pending" ||
              normalizedState === "i" ||
              normalizedState === "running" ||
              (!isSuccess && !isFailed && !isWarning && !isSkipped);

            const displayError = resolveTableRowDisplayError(
              table,
              nodeOrBatchErrorFallback,
            );
            const showErrorTooltip =
              isFailed || (isSkipped && Boolean(displayError));

            // Format times if available
            const startTime = table.start_time
              ? format(new Date(table.start_time), dateTimeFormat)
              : "--";
            const endTime = table.end_time
              ? format(new Date(table.end_time), dateTimeFormat)
              : "--";

            // In progress: staging_records_count (cumulative merged rows for Databricks,
            // staging inventory for deferred-merge destinations). Completed: data_transfer total.
            const isTerminal =
              isSuccess ||
              isFailed ||
              ["completed", "success", "failed", "s", "f", "e"].includes(
                normalizedState,
              );
            const stagingRecordsDisplay = (() => {
              if (isTerminal) {
                if (
                  table.record_count !== undefined &&
                  table.record_count !== null
                ) {
                  return table.record_count;
                }
              }
              if (
                table.staging_records_count !== undefined &&
                table.staging_records_count !== null
              ) {
                return table.staging_records_count;
              }
              if (
                table.record_count !== undefined &&
                table.record_count !== null
              ) {
                return table.record_count;
              }
              return "--";
            })();

            return (
              <Table.Row key={index} bg="white" _hover={{ bg: "gray.50" }}>
                <Table.Cell
                  textAlign="center"
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                >
                  <Flex alignItems="center" justifyContent="center">
                    {isSuccess && (
                      <Image
                        src={CheckIcon}
                        boxSize="20px"
                        objectFit="contain"
                      />
                    )}
                    {isFailed && (
                      <TableFailureTooltip errorText={displayError}>
                        <Image
                          src={ErrorIcon}
                          boxSize="20px"
                          objectFit="contain"
                        />
                      </TableFailureTooltip>
                    )}
                    {isWarning && (
                      <Box color="orange.500">
                        <MdWarning size={20} />
                      </Box>
                    )}
                    {isSkipped &&
                      (showErrorTooltip ? (
                        <Tooltip
                          content={
                            <Text
                              fontSize="xs"
                              fontWeight="medium"
                              wordBreak="break-word"
                            >
                              {displayError}
                            </Text>
                          }
                          showArrow
                          contentProps={{
                            bg: "gray.800",
                            color: "white",
                            p: 3,
                            borderRadius: "md",
                            maxW: "500px",
                          }}
                        >
                          <Box color="gray.500" cursor="help">
                            <MdWarning size={20} />
                          </Box>
                        </Tooltip>
                      ) : (
                        <Box color="gray.500" title="Skipped">
                          <MdWarning size={20} />
                        </Box>
                      ))}
                    {isPending && (
                      <Image
                        src={SandtimeIcon}
                        boxSize="20px"
                        objectFit="contain"
                      />
                    )}
                  </Flex>
                </Table.Cell>
                <Table.Cell
                  fontWeight="medium"
                  color="gray.800"
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                >
                  {isFailed ? (
                    <TableFailureTooltip errorText={displayError}>
                      <Flex direction="column" minW={0}>
                        <Text color="red.700">{table.table_name}</Text>
                        {destByTable.get(table.table_name.toLowerCase()) && (
                          <Text fontSize="xs" color="gray.500" truncate>
                            {destByTable.get(table.table_name.toLowerCase())}
                          </Text>
                        )}
                      </Flex>
                    </TableFailureTooltip>
                  ) : (
                    <Flex direction="column" minW={0}>
                      <Text>{table.table_name}</Text>
                      {destByTable.get(table.table_name.toLowerCase()) && (
                        <Text fontSize="xs" color="gray.500" truncate>
                          {destByTable.get(table.table_name.toLowerCase())}
                        </Text>
                      )}
                    </Flex>
                  )}
                </Table.Cell>
                <Table.Cell
                  color="gray.700"
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                >
                  <Text whiteSpace="nowrap" fontSize="xs">
                    {startTime}
                  </Text>
                </Table.Cell>
                <Table.Cell
                  color="gray.700"
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                >
                  <Text whiteSpace="nowrap" fontSize="xs">
                    {endTime}
                  </Text>
                </Table.Cell>
                <Table.Cell textAlign="right" color="gray.700" py={0.5}>
                  {stagingRecordsDisplay}
                </Table.Cell>
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};

export default MigrationProgressTable;
