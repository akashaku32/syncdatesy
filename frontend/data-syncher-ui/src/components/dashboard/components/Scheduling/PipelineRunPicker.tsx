import { useMemo, useState } from "react";

import { Box, Button, Flex, Menu, Portal, Text } from "@chakra-ui/react";

import { MdExpandMore } from "react-icons/md";

import { type PipelineRunSummary } from "@/types/pipeline";

import {
  pipelineStatusColor,
  resolvePipelineRunStatus,
} from "./pipelineRunHelpers";

export type RunStatusFilter = "all" | "completed" | "running" | "failed";

const STATUS_FILTERS: Array<{ value: RunStatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "completed", label: "Completed" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
];

type PipelineRunPickerProps = {
  runs: PipelineRunSummary[];
  selectedRunId: number | null;
  onSelect: (_runId: number) => void;
  width?: string | number;
};

const formatRunLabel = (run: PipelineRunSummary) => {
  const status = resolvePipelineRunStatus(run);
  const when = run.started_at
    ? ` · ${new Date(run.started_at).toLocaleString()}`
    : "";
  return `#${run.pipeline_run_id} · ${status}${when}`;
};

const matchesStatusFilter = (
  run: PipelineRunSummary,
  filter: RunStatusFilter,
) => {
  if (filter === "all") return true;
  const status = resolvePipelineRunStatus(run);
  if (filter === "failed") {
    return status === "failed" || status === "timeout";
  }
  if (filter === "running") {
    return (
      status === "running" || status === "in_progress" || status === "queued"
    );
  }
  return status === filter;
};

const PipelineRunPicker = ({
  runs,
  selectedRunId,
  onSelect,
  width = "280px",
}: PipelineRunPickerProps) => {
  const [statusFilter, setStatusFilter] = useState<RunStatusFilter>("all");

  const selected =
    runs.find((r) => r.pipeline_run_id === selectedRunId) ?? runs[0] ?? null;

  const filteredRuns = useMemo(
    () => runs.filter((r) => matchesStatusFilter(r, statusFilter)),
    [runs, statusFilter],
  );

  return (
    <Menu.Root positioning={{ sameWidth: true }}>
      <Menu.Trigger asChild>
        <Button
          size="sm"
          variant="outline"
          width={width}
          maxW="40vw"
          flexShrink={1}
          justifyContent="space-between"
          fontWeight="normal"
          px={2.5}
          h="32px"
          bg="white"
          borderColor="gray.200"
          borderRadius="md"
          color="gray.800"
          _hover={{ bg: "gray.50", borderColor: "gray.300" }}
          _expanded={{ bg: "gray.50", borderColor: "gray.400" }}
          aria-label="Select pipeline run"
        >
          <Text
            truncate
            fontSize="sm"
            color={selected ? "gray.800" : "gray.500"}
            flex="1"
            minW={0}
            textAlign="left"
          >
            {selected ? formatRunLabel(selected) : "Draft canvas (edit)"}
          </Text>
          <Box
            as="span"
            color="gray.400"
            display="inline-flex"
            flexShrink={0}
            ml={1}
          >
            <MdExpandMore size={18} />
          </Box>
        </Button>
      </Menu.Trigger>
      <Portal>
        <Menu.Positioner>
          <Menu.Content
            minW={width}
            maxH="320px"
            overflow="hidden"
            py={0}
            bg="white"
            borderWidth={1}
            borderColor="gray.200"
            borderRadius="md"
            boxShadow="sm"
            display="flex"
            flexDirection="column"
          >
            <Flex
              gap={1}
              px={2}
              py={2}
              borderBottomWidth={1}
              borderColor="gray.100"
              flexWrap="wrap"
              flexShrink={0}
              onClick={(e) => e.stopPropagation()}
            >
              {STATUS_FILTERS.map((f) => {
                const active = statusFilter === f.value;
                return (
                  <Button
                    key={f.value}
                    size="xs"
                    h="24px"
                    px={2}
                    variant={active ? "solid" : "outline"}
                    colorPalette={active ? "brand" : "gray"}
                    fontWeight={active ? "medium" : "normal"}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setStatusFilter(f.value);
                    }}
                  >
                    {f.label}
                  </Button>
                );
              })}
            </Flex>

            <Box flex="1" minH={0} overflowY="auto" py={1}>
              {filteredRuns.length === 0 ? (
                <Text fontSize="sm" color="gray.500" px={3} py={3}>
                  No runs match this filter.
                </Text>
              ) : (
                filteredRuns.map((r) => {
                  const isSelected = r.pipeline_run_id === selectedRunId;
                  const status = resolvePipelineRunStatus(r);
                  const statusColor = pipelineStatusColor(status);
                  return (
                    <Menu.Item
                      key={r.pipeline_run_id}
                      value={String(r.pipeline_run_id)}
                      onClick={() => onSelect(r.pipeline_run_id)}
                      px={3}
                      py={2}
                      bg={isSelected ? "gray.50" : undefined}
                      borderLeftWidth={isSelected ? "2px" : "0"}
                      borderLeftColor={isSelected ? "gray.700" : "transparent"}
                      _highlighted={{ bg: "gray.50" }}
                    >
                      <Flex
                        alignItems="center"
                        justifyContent="space-between"
                        gap={3}
                        w="100%"
                        minW={0}
                      >
                        <Text
                          truncate
                          fontSize="sm"
                          fontWeight={isSelected ? "medium" : "normal"}
                          color="gray.800"
                          flex="1"
                          minW={0}
                        >
                          #{r.pipeline_run_id}
                          {r.started_at
                            ? ` · ${new Date(r.started_at).toLocaleString()}`
                            : ""}
                        </Text>
                        <Text
                          as="span"
                          fontSize="2xs"
                          color={`${statusColor}.700`}
                          flexShrink={0}
                          textTransform="capitalize"
                        >
                          {status}
                        </Text>
                      </Flex>
                    </Menu.Item>
                  );
                })
              )}
            </Box>
          </Menu.Content>
        </Menu.Positioner>
      </Portal>
    </Menu.Root>
  );
};

export default PipelineRunPicker;
