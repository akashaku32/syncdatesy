import { useMemo, useState } from "react";

import {
  Badge,
  Box,
  Button,
  Flex,
  IconButton,
  Skeleton,
  Tabs,
  Text,
} from "@chakra-ui/react";

import { MdClose, MdPlayArrow } from "react-icons/md";

import BatchExecutionPanel from "@/components/dashboard/components/Scheduling/BatchExecutionPanel";
import BatchNotificationPanel from "@/components/dashboard/components/Scheduling/BatchNotificationPanel";
import { toaster } from "@/components/ui/toaster";
import { getUiState } from "@/helpers/log";
import { useFetchBatchDetail } from "@/queryOptions/connector/schema/useBatches";
import { useRunPipeline } from "@/queryOptions/pipeline/usePipeline";
import {
  type PipelineDetail,
  type PipelineRunDetail,
  type PipelineRunNodeDetail,
} from "@/types/pipeline";

import { computeRootNodeIds, getParentBatchName } from "./pipelineLayout";
import {
  PLAN_FLOW_CAP_FALLBACK,
  isPlanFlowCapError,
  pipelineHasPublishedRunnableGraph,
  resolvePipelineNodeError,
  unwrapPipelineRunApiError,
} from "./pipelineRunHelpers";

type BatchOverviewPanelProps = {
  pipeline: PipelineDetail;
  nodeId: number;
  connectionId: number;
  batchId: number;
  connectionName: string;
  runNode?: PipelineRunNodeDetail | null;
  pipelineRun?: PipelineRunDetail | null;
  onRunStarted?: (_runId: number) => void;
  onClose: () => void;
};

function tableStatusBadgeColor(status: string) {
  const s = status.toLowerCase();
  if (s === "completed" || s === "success") return "green";
  if (s === "failed" || s === "error") return "red";
  if (s === "running" || s === "in_progress") return "blue";
  return "gray";
}

const BatchOverviewPanel = ({
  pipeline,
  nodeId,
  connectionId,
  batchId,
  connectionName,
  runNode,
  pipelineRun,
  onRunStarted,
  onClose,
}: BatchOverviewPanelProps) => {
  const nodeError =
    runNode && pipelineRun
      ? resolvePipelineNodeError(pipelineRun, runNode)
      : runNode?.error?.trim() || null;
  const { data: batch, isLoading } = useFetchBatchDetail(connectionId, batchId);
  const runPipeline = useRunPipeline();
  const [activeTab, setActiveTab] = useState("overview");

  const rootNodeIds = useMemo(
    () =>
      pipeline.root_node_ids ??
      computeRootNodeIds(pipeline.nodes, pipeline.edges),
    [pipeline],
  );
  const isRoot = rootNodeIds.includes(nodeId);
  const parentBatchName = useMemo(
    () => getParentBatchName(nodeId, pipeline.nodes, pipeline.edges),
    [nodeId, pipeline.nodes, pipeline.edges],
  );

  const pipelineNode = pipeline.nodes.find((n) => n.id === nodeId);
  const pipelinePaused = pipeline.status === "paused";

  const tableStatusMap = useMemo(() => {
    const map = new Map<string, string>();
    const tables = runNode?.migration_status?.tables ?? [];
    for (const t of tables) {
      const uiState = getUiState(t.status_icon, t.status, t.error_message);
      map.set(t.table_name, uiState);
    }
    return map;
  }, [runNode]);

  const canRunPipeline =
    pipeline.nodes.length > 0 && pipelineHasPublishedRunnableGraph(pipeline);

  const handleRunPipeline = async () => {
    if (!pipelineHasPublishedRunnableGraph(pipeline)) {
      toaster.error({
        title: "Validate before running",
        description:
          "Validate the Draft flow first, then run the published flow.",
      });
      return;
    }
    try {
      const result = await runPipeline.mutateAsync(pipeline.id);
      onRunStarted?.(result.pipeline_run_id);
      toaster.success({
        title: "Pipeline started",
        description: result.message,
      });
    } catch (err: unknown) {
      // api-client rejects with response.data (not AxiosError) — unwrap both shapes.
      const { data, status } = unwrapPipelineRunApiError(err);
      if (isPlanFlowCapError(data, status)) {
        toaster.warning({
          title: "Flow limit reached",
          description: data?.warning || data?.error || PLAN_FLOW_CAP_FALLBACK,
        });
        return;
      }
      const message = data?.error ?? "Failed to run pipeline.";
      toaster.error({ title: "Failed to run pipeline", description: message });
    }
  };

  return (
    <Flex
      direction="column"
      h="100%"
      borderWidth={1}
      borderRadius="lg"
      bg="white"
      overflow="hidden"
      minH={0}
    >
      <Flex
        alignItems="center"
        justifyContent="space-between"
        px={3}
        py={2}
        borderBottomWidth={1}
        borderColor="gray.200"
        flexShrink={0}
      >
        <Text fontSize="sm" fontWeight="semibold" color="gray.800">
          {batch?.name ?? "Batch"}
        </Text>
        <IconButton
          aria-label="Close panel"
          size="xs"
          variant="ghost"
          onClick={onClose}
        >
          <MdClose />
        </IconButton>
      </Flex>

      <Tabs.Root
        value={activeTab}
        onValueChange={(d) => setActiveTab(d.value)}
        variant="line"
        colorPalette="brand"
        flex="1"
        display="flex"
        flexDirection="column"
        minH={0}
      >
        <Tabs.List px={2} borderBottomWidth={1} borderColor="gray.100" gap={0}>
          <Tabs.Trigger value="overview" fontSize="xs" py={2} px={2.5}>
            Overview
          </Tabs.Trigger>
          <Tabs.Trigger value="execution" fontSize="xs" py={2} px={2.5}>
            Execution
          </Tabs.Trigger>
          <Tabs.Trigger value="email" fontSize="xs" py={2} px={2.5}>
            Email
          </Tabs.Trigger>
          <Tabs.Trigger value="tables" fontSize="xs" py={2} px={2.5}>
            Tables
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="overview" flex="1" overflowY="auto" p={3} minH={0}>
          {isLoading && (
            <Flex direction="column" gap={3}>
              <Skeleton height={6} />
              <Skeleton height={4} />
              <Skeleton height={24} />
            </Flex>
          )}

          {!isLoading && batch && (
            <Flex direction="column" gap={3}>
              <Box>
                <Text fontSize="xs" color="gray.500">
                  {connectionName}
                </Text>
              </Box>

              <Flex gap={1.5} flexWrap="wrap">
                <Badge colorPalette="purple" variant="outline" size="sm">
                  Pipeline: {pipeline.name}
                </Badge>
                {!isRoot && (
                  <Badge colorPalette="gray" variant="subtle" size="sm">
                    After: {parentBatchName ?? "parent"}
                  </Badge>
                )}
                <Badge colorPalette="gray" variant="subtle" size="sm">
                  {batch.table_count} table{batch.table_count === 1 ? "" : "s"}
                </Badge>
                <Badge colorPalette="gray" variant="outline" size="sm">
                  Process:{" "}
                  {pipelineNode?.execution_order ?? batch.execution_order}
                </Badge>
                {pipelinePaused && (
                  <Badge colorPalette="orange" variant="subtle" size="sm">
                    Pipeline paused
                  </Badge>
                )}
                {runNode?.status && (
                  <Badge
                    colorPalette={tableStatusBadgeColor(runNode.status)}
                    variant="subtle"
                    size="sm"
                  >
                    Run: {runNode.status}
                  </Badge>
                )}
              </Flex>

              <Box
                borderWidth={1}
                borderColor="gray.200"
                borderRadius="md"
                p={2.5}
                bg="gray.50"
              >
                <Text fontSize="xs" color="gray.700">
                  {isRoot ? (
                    <>
                      In pipeline{" "}
                      <Text as="span" fontWeight="semibold">
                        {pipeline.name}
                      </Text>
                      . Runs in the first wave after{" "}
                      <Text as="span" fontWeight="semibold">
                        Start
                      </Text>{" "}
                      ({pipelineNode?.execution_order ?? batch.execution_order}{" "}
                      process). Set timing on the Start node Schedule tab.
                    </>
                  ) : (
                    <>
                      In pipeline{" "}
                      <Text as="span" fontWeight="semibold">
                        {pipeline.name}
                      </Text>
                      . Runs after{" "}
                      <Text as="span" fontWeight="semibold">
                        {parentBatchName ?? "parent batch"}
                      </Text>{" "}
                      ({pipelineNode?.execution_order ?? batch.execution_order}{" "}
                      process). Pipeline schedule is configured on the Start
                      node.
                    </>
                  )}
                </Text>
              </Box>

              {nodeError && (
                <Text fontSize="xs" color="red.600">
                  {nodeError}
                </Text>
              )}

              {runNode && (
                <Text fontSize="2xs" color="gray.500">
                  Table progress and per-task logs are in the center Execution
                  logs tab.
                </Text>
              )}

              <Button
                size="sm"
                colorPalette={canRunPipeline ? "brand" : "gray"}
                variant={canRunPipeline ? "solid" : "outline"}
                onClick={handleRunPipeline}
                loading={runPipeline.isPending}
                disabled={!canRunPipeline}
                title={
                  !canRunPipeline
                    ? "Validate the Draft flow before running"
                    : undefined
                }
              >
                <MdPlayArrow />
                Run pipeline now
              </Button>
            </Flex>
          )}
        </Tabs.Content>

        <Tabs.Content
          value="execution"
          flex="1"
          overflowY="auto"
          p={3}
          minH={0}
        >
          {!isLoading && batch && (
            <BatchExecutionPanel
              pipelineId={pipeline.id}
              nodeId={nodeId}
              pipelineNode={pipelineNode}
              disabled={pipelinePaused}
              embedded
            />
          )}
        </Tabs.Content>

        <Tabs.Content value="email" flex="1" overflowY="auto" p={3} minH={0}>
          {!isLoading && batch && (
            <BatchNotificationPanel
              pipelineId={pipeline.id}
              nodeId={nodeId}
              pipelineNode={pipelineNode}
              disabled={pipelinePaused}
              embedded
            />
          )}
        </Tabs.Content>

        <Tabs.Content value="tables" flex="1" overflowY="auto" p={3} minH={0}>
          {isLoading && <Skeleton height="120px" />}

          {!isLoading && batch && (
            <Flex direction="column" gap={2} minH={0}>
              {runNode?.status && (
                <Badge
                  size="sm"
                  alignSelf="flex-start"
                  colorPalette={tableStatusBadgeColor(runNode.status)}
                  variant="subtle"
                >
                  Task: {runNode.status}
                </Badge>
              )}

              {nodeError && (
                <Text fontSize="xs" color="red.600">
                  {nodeError}
                </Text>
              )}

              {batch.tables.length === 0 ? (
                <Text fontSize="xs" color="gray.500">
                  No tables in this batch.
                </Text>
              ) : (
                <Box
                  borderWidth={1}
                  borderColor="gray.200"
                  borderRadius="md"
                  flex="1"
                  overflowY="auto"
                >
                  {batch.tables
                    .slice()
                    .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
                    .map((t, index) => {
                      const runStatus = tableStatusMap.get(t.table_name);
                      return (
                        <Flex
                          key={t.table_name}
                          px={2.5}
                          py={1.5}
                          gap={2}
                          alignItems="center"
                          bg={index % 2 === 0 ? "gray.50" : "white"}
                          borderBottomWidth={
                            index < batch.tables.length - 1 ? 1 : 0
                          }
                          borderColor="gray.100"
                        >
                          <Text
                            fontSize="xs"
                            flex="1"
                            truncate
                            title={t.table_name}
                          >
                            {t.table_name}
                          </Text>
                          {runStatus && (
                            <Badge
                              size="sm"
                              variant="subtle"
                              colorPalette={tableStatusBadgeColor(runStatus)}
                            >
                              {runStatus}
                            </Badge>
                          )}
                        </Flex>
                      );
                    })}
                </Box>
              )}
            </Flex>
          )}
        </Tabs.Content>
      </Tabs.Root>
    </Flex>
  );
};

export default BatchOverviewPanel;
