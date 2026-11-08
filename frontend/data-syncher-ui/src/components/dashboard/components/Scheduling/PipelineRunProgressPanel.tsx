import { useEffect, useMemo, useState } from "react";

import { Badge, Box, Flex, Progress, Text } from "@chakra-ui/react";

import { type PipelineRunDetail } from "@/types/pipeline";

import {
  computeNodeProgress,
  computeRunDurationMs,
  computeTableProgress,
  formatElapsedDuration,
  formatPipelineRunTimestampCompact,
  pipelineStatusColor,
  resolvePipelineRunStatus,
} from "./pipelineRunHelpers";

type PipelineRunProgressPanelProps = {
  run: PipelineRunDetail;
};

type RunMetricProgressProps = {
  label: string;
  value: number;
  completed: number;
  total: number;
  colorPalette: string;
  animated?: boolean;
  barHeight?: string;
};

const RunMetricProgress = ({
  label,
  value,
  completed,
  total,
  colorPalette,
  animated = false,
  barHeight = "6px",
}: RunMetricProgressProps) => (
  <Flex alignItems="center" gap={2}>
    <Text
      fontSize="2xs"
      fontWeight="medium"
      color="gray.500"
      w="40px"
      flexShrink={0}
      textTransform="uppercase"
      letterSpacing="wider"
    >
      {label}
    </Text>
    <Box flex="1" minW={0}>
      <Progress.Root
        value={value}
        variant="outline"
        shape="full"
        colorPalette={colorPalette}
        striped={animated}
        animated={animated}
      >
        <Progress.Track
          h={barHeight}
          bg="white"
          borderWidth="1px"
          borderColor="gray.200"
          boxShadow="inset 0 1px 2px rgba(15, 23, 42, 0.06)"
        >
          <Progress.Range
            borderRadius="full"
            boxShadow="0 1px 2px rgba(15, 23, 42, 0.12)"
          />
        </Progress.Track>
      </Progress.Root>
    </Box>
    <Text
      fontSize="2xs"
      fontWeight="medium"
      color="gray.600"
      w="36px"
      flexShrink={0}
      textAlign="right"
      fontVariantNumeric="tabular-nums"
    >
      {completed}/{total}
    </Text>
  </Flex>
);

const PipelineRunProgressPanel = ({ run }: PipelineRunProgressPanelProps) => {
  const { overall } = run;
  const displayStatus = resolvePipelineRunStatus(run);
  const nodeProgress = computeNodeProgress(overall);
  const tableProgress = computeTableProgress(overall);
  const statusColor = pipelineStatusColor(displayStatus);
  const isQueued = displayStatus === "queued";
  const isRunning =
    displayStatus === "running" || displayStatus === "in_progress";
  const isActive = isRunning || isQueued;

  const activeNodeIds = run.current_node_ids?.length
    ? run.current_node_ids
    : run.current_node_id !== null && run.current_node_id !== undefined
      ? [run.current_node_id]
      : [];
  const runningNodes = run.nodes.filter((n) =>
    activeNodeIds.includes(n.node_id),
  );
  const runningLabel =
    isRunning && runningNodes.length
      ? runningNodes.map((n) => n.batch_name).join(", ")
      : null;

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!isActive || !run.started_at) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isActive, run.started_at]);

  const startedLabel = formatPipelineRunTimestampCompact(run.started_at);
  const finishedLabel = formatPipelineRunTimestampCompact(run.finished_at);
  const durationMs = computeRunDurationMs(
    run.started_at,
    run.finished_at,
    nowMs,
  );
  const durationLabel =
    durationMs !== null ? formatElapsedDuration(durationMs) : null;

  const centerParts = useMemo(() => {
    const parts: string[] = [];
    if (isQueued) {
      parts.push("Queued — waiting for a free concurrent flow slot");
    } else if (runningLabel) {
      parts.push(`Running: ${runningLabel}`);
    }
    if (startedLabel) parts.push(`Started ${startedLabel}`);
    if (finishedLabel) {
      parts.push(`Ended ${finishedLabel}`);
      if (durationLabel) parts.push(durationLabel);
    } else if (isRunning && durationLabel) {
      parts.push(`Elapsed ${durationLabel}`);
    }
    return parts;
  }, [
    runningLabel,
    startedLabel,
    finishedLabel,
    durationLabel,
    isRunning,
    isQueued,
  ]);

  return (
    <Box
      borderWidth={1}
      borderColor="gray.200"
      borderRadius="md"
      px={3}
      py={2.5}
      bg="gray.50"
    >
      <Flex alignItems="center" gap={3} mb={2}>
        <Flex alignItems="center" gap={2} flexShrink={0} minW="100px">
          <Text
            fontSize="xs"
            fontWeight="semibold"
            color="gray.700"
            flexShrink={0}
          >
            Run #{run.pipeline_run_id}
          </Text>
          <Badge
            colorPalette={statusColor}
            size="sm"
            variant="subtle"
            flexShrink={0}
          >
            {displayStatus}
          </Badge>
        </Flex>

        <Text
          flex="1"
          minW={0}
          fontSize="2xs"
          color="gray.600"
          textAlign="center"
          truncate
          fontVariantNumeric="tabular-nums"
          title={centerParts.length ? centerParts.join(" · ") : undefined}
        >
          {centerParts.join(" · ")}
        </Text>

        <Text
          fontSize="2xs"
          color="gray.500"
          flexShrink={0}
          textAlign="right"
          fontVariantNumeric="tabular-nums"
        >
          {overall.nodes_completed}/{overall.nodes_total} nodes ·{" "}
          {overall.tables_completed + (overall.tables_failed ?? 0)}/
          {overall.tables_total} tables
          {!isRunning && (overall.tables_failed ?? 0) > 0
            ? ` (${overall.tables_failed} failed)`
            : ""}
        </Text>
      </Flex>

      <Flex direction="column" gap={1.5}>
        <RunMetricProgress
          label="Nodes"
          value={nodeProgress}
          completed={overall.nodes_completed}
          total={overall.nodes_total}
          colorPalette={statusColor}
          animated={isActive}
        />
        {overall.tables_total > 0 && (
          <RunMetricProgress
            label="Tables"
            value={tableProgress}
            completed={overall.tables_completed}
            total={overall.tables_total}
            colorPalette="blue"
            animated={isActive}
            barHeight="5px"
          />
        )}
      </Flex>

      {(displayStatus === "failed" || displayStatus === "timeout") &&
        run.error?.trim() && (
          <>
            {overall.nodes_completed > 0 && overall.nodes_failed > 0 && (
              <Text fontSize="xs" color="gray.600" mt={2}>
                {overall.nodes_completed} of {overall.nodes_total} batch(es)
                finished before the pipeline stopped.
              </Text>
            )}
            <Text fontSize="xs" color="red.600" mt={2}>
              {run.error.trim()}
            </Text>
          </>
        )}
    </Box>
  );
};

export default PipelineRunProgressPanel;
