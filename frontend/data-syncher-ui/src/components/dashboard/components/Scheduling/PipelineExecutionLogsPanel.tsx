import { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Box,
  Flex,
  NativeSelect,
  Skeleton,
  Text,
} from "@chakra-ui/react";

import MigrationProgressTable from "@/components/dashboard/components/Connectors/components/ConnectorDetails/components/Tabs/Overview/components/MigrationProgressTable";
import useMigrationStatusWS from "@/hooks/useMigrationStatusWS";
import useFetchConnectorActivityDetails from "@/queryOptions/connector/useFetchConnectorActivityDetails";
import {
  type PipelineRunDetail,
  type PipelineRunNodeDetail,
  type PipelineRunSummary,
} from "@/types/pipeline";

import {
  executionLogNodeBadge,
  pickDefaultNodeTab,
  pipelineNodeStartupMessage,
  resolvePipelineNodeError,
  resolvePipelineRunMode,
  resolvePipelineRunStatus,
  summarizePipelineTableErrors,
} from "./pipelineRunHelpers";
import { useQueryClient } from "@tanstack/react-query";

type PipelineExecutionLogsPanelProps = {
  run: PipelineRunDetail;
  runs?: PipelineRunSummary[];
  initialProcessName?: string | null;
  onRunSelect?: (_runId: number) => void;
};

const PipelineNodeExecutionTab = ({
  node,
  run,
  runMode,
}: {
  node: PipelineRunNodeDetail;
  run: PipelineRunDetail;
  runMode: ReturnType<typeof resolvePipelineRunMode>;
}) => {
  const queryClient = useQueryClient();
  const runStatus = resolvePipelineRunStatus(run);
  const runTerminal = ["failed", "completed", "timeout"].includes(runStatus);
  const migrationSessionId = node.migration_session_id ?? null;

  useMigrationStatusWS(
    runTerminal ? null : migrationSessionId,
    node.connection_id,
  );

  const { data: migrationStatus, isLoading } = useFetchConnectorActivityDetails(
    {
      migrationId: migrationSessionId ?? undefined,
      connectionId: node.connection_id,
    },
  );

  useEffect(() => {
    if (!runTerminal || !migrationSessionId || !node.connection_id) return;
    void queryClient.invalidateQueries({
      queryKey: [
        "connectorActivityDetails",
        migrationSessionId,
        node.connection_id,
      ],
    });
  }, [runTerminal, migrationSessionId, node.connection_id, queryClient]);

  const progressTables = useMemo(() => {
    // Prefer live/refetched activity details (includes per-table start/end times).
    if (migrationStatus?.tables?.length) {
      return migrationStatus.tables;
    }
    return node.migration_status?.tables ?? [];
  }, [migrationStatus?.tables, node.migration_status?.tables]) as Parameters<
    typeof MigrationProgressTable
  >[0]["tables"];

  const nodeError =
    summarizePipelineTableErrors(migrationStatus?.tables) ??
    summarizePipelineTableErrors(node.migration_status?.tables) ??
    resolvePipelineNodeError(run, node);

  const startupMessage = pipelineNodeStartupMessage(run, node);
  const nodeBadge = executionLogNodeBadge(runMode, node, run);

  return (
    <Flex direction="column" gap={2} h="100%" minH={0}>
      <Flex alignItems="center" gap={2} flexWrap="wrap" flexShrink={0}>
        {nodeBadge ? (
          <Badge
            colorPalette={nodeBadge.colorPalette}
            size="sm"
            variant={nodeBadge.variant}
          >
            {nodeBadge.label}
          </Badge>
        ) : runMode === "published" && startupMessage ? (
          <Badge colorPalette="blue" size="sm" variant="subtle">
            starting
          </Badge>
        ) : runMode === "published" ? (
          <Badge colorPalette="gray" size="sm" variant="outline">
            pending
          </Badge>
        ) : null}
        {migrationSessionId !== null && migrationSessionId !== undefined && (
          <Text fontSize="2xs" color="gray.500">
            Session {migrationSessionId}
          </Text>
        )}
        <Text fontSize="2xs" color="gray.500">
          {node.table_count} table{node.table_count === 1 ? "" : "s"}
        </Text>
      </Flex>

      {migrationStatus?.overall_warning_message && (
        <Text fontSize="xs" color="orange.700" flexShrink={0}>
          {migrationStatus.overall_warning_message}
        </Text>
      )}

      {nodeError && (
        <Text fontSize="xs" color="red.600" flexShrink={0}>
          {nodeError}
        </Text>
      )}

      {isLoading && !(progressTables?.length ?? 0) ? (
        <Skeleton flex="1" minH="120px" />
      ) : progressTables?.length ? (
        <Flex flex="1" minH={0} overflowY="auto">
          <MigrationProgressTable
            tables={progressTables}
            progressCountSource={
              migrationStatus?.progress_count_source ??
              node.migration_status?.progress_count_source
            }
            nodeOrBatchErrorFallback={nodeError}
          />
        </Flex>
      ) : runMode === "published" ? (
        <Flex flex="1" alignItems="center" justifyContent="center" minH="120px">
          <Text fontSize="sm" color={nodeError ? "red.600" : "gray.500"}>
            {nodeError
              ? nodeError
              : startupMessage
                ? startupMessage
                : node.status === "pending" || node.status === "waiting"
                  ? runStatus === "failed" || runStatus === "timeout"
                    ? "This task did not start before the flow failed."
                    : "Waiting for this task to start…"
                  : "No table progress yet for this task."}
          </Text>
        </Flex>
      ) : (
        <Flex flex="1" alignItems="center" justifyContent="center" minH="120px">
          <Text fontSize="sm" color="gray.500">
            Draft run — migration status is shown for published runs only.
          </Text>
        </Flex>
      )}
    </Flex>
  );
};

const PipelineExecutionLogsPanel = ({
  run,
  runs = [],
  initialProcessName,
  onRunSelect,
}: PipelineExecutionLogsPanelProps) => {
  const nodes = useMemo(
    () =>
      [...run.nodes].sort((a, b) => {
        const aStart = a.started_at
          ? Date.parse(a.started_at)
          : Number.MAX_SAFE_INTEGER;
        const bStart = b.started_at
          ? Date.parse(b.started_at)
          : Number.MAX_SAFE_INTEGER;
        if (aStart !== bStart) return aStart - bStart;
        return a.node_id - b.node_id;
      }),
    [run.nodes],
  );

  const defaultTab = useMemo(
    () => pickDefaultNodeTab(nodes, run.current_node_id, run.current_node_ids),
    [nodes, run.current_node_id, run.current_node_ids],
  );
  const initialProcessTab = useMemo(() => {
    if (!initialProcessName) return null;
    const normalizedName = initialProcessName.trim().toLowerCase();
    const match = nodes.find(
      (node) =>
        node.batch_name.trim().toLowerCase() === normalizedName ||
        node.node_label.trim().toLowerCase() === normalizedName,
    );
    return match ? String(match.node_id) : null;
  }, [initialProcessName, nodes]);

  const [activeNodeTab, setActiveNodeTab] = useState(
    initialProcessTab ?? defaultTab,
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset tab when run/process deep link changes
    setActiveNodeTab(initialProcessTab ?? defaultTab);
  }, [run.pipeline_run_id, initialProcessTab, defaultTab]);

  const activeNode =
    nodes.find((n) => String(n.node_id) === activeNodeTab) ?? nodes[0];
  const runMode = resolvePipelineRunMode(run);

  if (nodes.length === 0) {
    return (
      <Flex alignItems="center" justifyContent="center" h="100%" minH="200px">
        <Text fontSize="sm" color="gray.500">
          No tasks in this run.
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" h="100%" minH={0}>
      {(runs.length > 0 || onRunSelect) && (
        <Flex
          px={3}
          py={2}
          borderBottomWidth={1}
          borderColor="gray.200"
          alignItems="center"
          gap={2}
          flexShrink={0}
        >
          <Text fontSize="xs" color="gray.600" whiteSpace="nowrap">
            Run
          </Text>
          <NativeSelect.Root size="sm" flex="1" maxW="320px">
            <NativeSelect.Field
              value={String(run.pipeline_run_id)}
              onChange={(e) => {
                const id = Number(e.target.value);
                if (id && onRunSelect) onRunSelect(id);
              }}
            >
              {runs.map((r) => (
                <option key={r.pipeline_run_id} value={r.pipeline_run_id}>
                  #{r.pipeline_run_id} · {r.status}
                  {r.started_at
                    ? ` · ${new Date(r.started_at).toLocaleString()}`
                    : ""}
                </option>
              ))}
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Flex>
      )}

      <Flex flex="1" minH={0} overflow="hidden">
        <Flex
          direction="column"
          w={{ base: "140px", md: "200px" }}
          minW="140px"
          maxW="240px"
          flexShrink={0}
          borderRightWidth={1}
          borderColor="gray.200"
          overflowY="auto"
          bg="gray.50"
        >
          {nodes.map((node) => {
            const isActive = String(node.node_id) === activeNodeTab;
            const nodeBadge = executionLogNodeBadge(runMode, node, run);
            return (
              <Box
                key={node.node_id}
                as="button"
                w="100%"
                textAlign="left"
                px={3}
                py={2.5}
                borderBottomWidth={1}
                borderColor="gray.100"
                bg={isActive ? "white" : "transparent"}
                borderLeftWidth={isActive ? 3 : 0}
                borderLeftColor={isActive ? "brand.500" : "transparent"}
                cursor="pointer"
                transition="background 0.15s"
                _hover={{ bg: isActive ? "white" : "gray.100" }}
                onClick={() => setActiveNodeTab(String(node.node_id))}
              >
                <Text
                  fontSize="xs"
                  fontWeight={isActive ? "semibold" : "medium"}
                  color="gray.800"
                  truncate
                  title={node.batch_name}
                  mb={1}
                >
                  {node.batch_name}
                </Text>
                {nodeBadge ? (
                  <Badge
                    colorPalette={nodeBadge.colorPalette}
                    size="sm"
                    variant={nodeBadge.variant}
                  >
                    {nodeBadge.label}
                  </Badge>
                ) : null}
              </Box>
            );
          })}
        </Flex>

        <Flex flex="1" minH={0} overflowY="auto" p={3}>
          {activeNode && (
            <PipelineNodeExecutionTab
              node={activeNode}
              run={run}
              runMode={runMode}
            />
          )}
        </Flex>
      </Flex>
    </Flex>
  );
};

export default PipelineExecutionLogsPanel;
