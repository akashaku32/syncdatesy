import { Fragment, useMemo } from "react";

import { Box, Button, Flex, Menu, Portal, Text } from "@chakra-ui/react";

import { MdExpandMore } from "react-icons/md";

import { type PipelineDetail } from "@/types/pipeline";

type PipelinePickerProps = {
  pipelines: PipelineDetail[];
  selectedPipelineId: number | null;
  onSelect: (_pipelineId: number | null) => void;
  /** Prefetch runs when hovering a pipeline in the menu. */
  onPipelineHover?: (_pipelineId: number) => void;
  /** Trigger button width; grows up to maxWidth for long names. */
  width?: string | number;
  maxWidth?: string | number;
};

type PipelineListGroup = "running" | "queued" | "active" | "paused";

const SECTION_LABELS: Record<PipelineListGroup, string> = {
  running: "Running now",
  queued: "Queued",
  active: "Active",
  paused: "Paused",
};

const DEFAULT_WIDTH = "min(280px, 42vw)";
const DEFAULT_MAX_WIDTH = "min(320px, 48vw)";

export const isPipelineRunning = (pipeline: PipelineDetail) =>
  pipeline.latest_run_status === "running";

export const isPipelineQueued = (pipeline: PipelineDetail) =>
  pipeline.latest_run_status === "queued";

/** Flow-level pause (toolbar Pause/Resume). Independent of schedule_paused. */
export const isPipelineFlowPaused = (pipeline: PipelineDetail): boolean =>
  pipeline.status === "paused";

export const getPipelineListGroup = (
  pipeline: PipelineDetail,
): PipelineListGroup => {
  if (isPipelineRunning(pipeline)) return "running";
  if (isPipelineQueued(pipeline)) return "queued";
  if (isPipelineFlowPaused(pipeline)) return "paused";
  return "active";
};

const GROUP_ORDER: Record<PipelineListGroup, number> = {
  running: 0,
  queued: 1,
  active: 2,
  paused: 3,
};

export const sortPipelinesForPicker = (
  pipelines: PipelineDetail[],
): PipelineDetail[] =>
  [...pipelines].sort((a, b) => {
    const groupDiff =
      GROUP_ORDER[getPipelineListGroup(a)] -
      GROUP_ORDER[getPipelineListGroup(b)];
    if (groupDiff !== 0) return groupDiff;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });

/** Green/orange dot = flow active or flow paused. */
const FlowStatusDot = ({ paused }: { paused: boolean }) => (
  <Box
    as="span"
    w="6px"
    h="6px"
    borderRadius="full"
    flexShrink={0}
    bg={paused ? "orange.500" : "green.600"}
    title={paused ? "Flow paused" : "Flow active"}
  />
);

const PulsingLiveDot = () => (
  <Box
    as="span"
    position="relative"
    w="6px"
    h="6px"
    flexShrink={0}
    display="inline-block"
  >
    <Box
      as="span"
      position="absolute"
      inset={0}
      borderRadius="full"
      bg="blue.400"
      opacity={0.45}
      css={{
        animation: "pipelineLivePing 1.4s cubic-bezier(0, 0, 0.2, 1) infinite",
        "@keyframes pipelineLivePing": {
          "0%, 100%": { transform: "scale(1)", opacity: 0.45 },
          "50%": { transform: "scale(1.85)", opacity: 0 },
        },
      }}
    />
    <Box
      as="span"
      position="absolute"
      inset="1px"
      borderRadius="full"
      bg="blue.500"
    />
  </Box>
);

/** Execution in progress — separate from schedule Active/Paused. */
const RunningLiveBadge = ({ compact = false }: { compact?: boolean }) => (
  <Flex
    alignItems="center"
    gap={0.5}
    px={compact ? 1 : 1.25}
    py={compact ? 0 : 0.5}
    borderRadius="full"
    bg="blue.50"
    borderWidth="1px"
    borderColor="blue.200"
    flexShrink={0}
    title="Flow execution in progress"
  >
    <PulsingLiveDot />
    <Text
      as="span"
      fontSize="2xs"
      fontWeight="semibold"
      color="blue.700"
      letterSpacing="0.03em"
    >
      Live
    </Text>
  </Flex>
);

/** Waiting for a plan flow slot. Not an execution in progress. */
const QueuedBadge = ({ compact = false }: { compact?: boolean }) => (
  <Flex
    alignItems="center"
    gap={0.5}
    px={compact ? 1 : 1.25}
    py={compact ? 0 : 0.5}
    borderRadius="full"
    bg="purple.50"
    borderWidth="1px"
    borderColor="purple.200"
    flexShrink={0}
    title="Waiting for a free flow slot on this plan"
  >
    <Box
      as="span"
      w="6px"
      h="6px"
      borderRadius="full"
      bg="purple.500"
      flexShrink={0}
    />
    <Text
      as="span"
      fontSize="2xs"
      fontWeight="semibold"
      color="purple.700"
      letterSpacing="0.03em"
    >
      Queued
    </Text>
  </Flex>
);

const FlowStatusLabel = ({ paused }: { paused: boolean }) => (
  <Text
    as="span"
    fontSize="2xs"
    color={paused ? "orange.700" : "green.700"}
    flexShrink={0}
    w="48px"
    textAlign="right"
    letterSpacing="0.02em"
    title={paused ? "Flow paused" : "Flow active"}
  >
    {paused ? "Paused" : "Active"}
  </Text>
);

const PipelineRightStatus = ({
  pipeline,
  showScheduleLabel = true,
  compact = false,
}: {
  pipeline: PipelineDetail;
  showScheduleLabel?: boolean;
  compact?: boolean;
}) => {
  if (isPipelineRunning(pipeline)) {
    return <RunningLiveBadge compact={compact} />;
  }
  if (isPipelineQueued(pipeline)) {
    return <QueuedBadge compact={compact} />;
  }
  if (!showScheduleLabel) return null;
  return <FlowStatusLabel paused={isPipelineFlowPaused(pipeline)} />;
};

const PipelinePicker = ({
  pipelines,
  selectedPipelineId,
  onSelect,
  onPipelineHover,
  width = DEFAULT_WIDTH,
  maxWidth = DEFAULT_MAX_WIDTH,
}: PipelinePickerProps) => {
  const selected = pipelines.find((p) => p.id === selectedPipelineId) ?? null;
  const selectedPaused = selected ? isPipelineFlowPaused(selected) : false;
  const selectedRunning = selected ? isPipelineRunning(selected) : false;
  const selectedQueued = selected ? isPipelineQueued(selected) : false;

  const groupedPipelines = useMemo(() => {
    const sorted = sortPipelinesForPicker(pipelines);
    const sections: Array<{
      key: PipelineListGroup;
      label: string;
      items: PipelineDetail[];
    }> = [];

    for (const pipeline of sorted) {
      const key = getPipelineListGroup(pipeline);
      const last = sections[sections.length - 1];
      if (!last || last.key !== key) {
        sections.push({ key, label: SECTION_LABELS[key], items: [pipeline] });
      } else {
        last.items.push(pipeline);
      }
    }

    return sections;
  }, [pipelines]);

  const triggerTitle = selected
    ? `${selected.name}${selectedRunning ? " · Run in progress" : ""}${selectedQueued ? " · Queued for a flow slot" : ""}${selectedPaused ? " · Flow paused" : ""}`
    : undefined;

  return (
    <Menu.Root positioning={{ sameWidth: true }}>
      <Menu.Trigger asChild>
        <Button
          size="sm"
          variant="outline"
          width={width}
          maxW={maxWidth}
          minW="200px"
          flexShrink={1}
          justifyContent="space-between"
          fontWeight="normal"
          px={2.5}
          h="32px"
          bg={
            selectedRunning ? "blue.50" : selectedQueued ? "purple.50" : "white"
          }
          borderColor={
            selectedRunning
              ? "blue.300"
              : selectedQueued
                ? "purple.300"
                : "gray.200"
          }
          borderRadius="md"
          color="gray.800"
          title={triggerTitle}
          aria-label={
            selected ? `Selected pipeline: ${selected.name}` : "Select pipeline"
          }
          _hover={{
            bg: selectedRunning
              ? "blue.100"
              : selectedQueued
                ? "purple.100"
                : "gray.50",
            borderColor: selectedRunning
              ? "blue.400"
              : selectedQueued
                ? "purple.400"
                : "gray.300",
          }}
          _expanded={{
            bg: selectedRunning
              ? "blue.100"
              : selectedQueued
                ? "purple.100"
                : "gray.50",
            borderColor: selectedRunning
              ? "blue.400"
              : selectedQueued
                ? "purple.400"
                : "gray.400",
          }}
        >
          <Flex alignItems="center" gap={2} minW={0} flex="1">
            {selected && <FlowStatusDot paused={selectedPaused} />}
            <Text
              truncate
              fontSize="sm"
              color={selected ? "gray.800" : "gray.500"}
              flex="1"
              minW={0}
              textAlign="left"
            >
              {selected?.name ?? "Select pipeline…"}
            </Text>
            {selected && (
              <PipelineRightStatus
                pipeline={selected}
                showScheduleLabel={false}
                compact
              />
            )}
          </Flex>
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
            maxH="280px"
            overflowY="auto"
            py={1}
            bg="white"
            borderWidth={1}
            borderColor="gray.200"
            borderRadius="md"
            boxShadow="sm"
            zIndex={1500}
          >
            <Menu.Item
              value="__none__"
              onClick={() => onSelect(null)}
              px={3}
              py={2}
              color="gray.500"
              fontSize="sm"
              _highlighted={{ bg: "gray.50" }}
            >
              Select pipeline…
            </Menu.Item>
            {groupedPipelines.map((section, sectionIndex) => (
              <Fragment key={section.key}>
                {sectionIndex > 0 && (
                  <Box
                    mx={3}
                    my={0.5}
                    borderTopWidth="1px"
                    borderColor="gray.100"
                  />
                )}
                <Box px={3} pt={sectionIndex === 0 ? 0.5 : 1} pb={0.5}>
                  <Text
                    fontSize="2xs"
                    fontWeight="semibold"
                    color={
                      section.key === "running"
                        ? "blue.600"
                        : section.key === "queued"
                          ? "purple.600"
                          : "gray.500"
                    }
                    textTransform="uppercase"
                    letterSpacing="0.06em"
                  >
                    {section.label}
                  </Text>
                </Box>
                {section.items.map((p) => {
                  const isSelected = p.id === selectedPipelineId;
                  const paused = isPipelineFlowPaused(p);
                  const running = isPipelineRunning(p);
                  const queued = isPipelineQueued(p);
                  return (
                    <Menu.Item
                      key={p.id}
                      value={String(p.id)}
                      onClick={() => onSelect(p.id)}
                      onMouseEnter={() => onPipelineHover?.(p.id)}
                      px={3}
                      py={2}
                      title={p.name}
                      bg={
                        isSelected
                          ? running
                            ? "blue.50"
                            : queued
                              ? "purple.50"
                              : "gray.50"
                          : undefined
                      }
                      borderLeftWidth={isSelected ? "2px" : "0"}
                      borderLeftColor={isSelected ? "gray.700" : "transparent"}
                      _highlighted={{
                        bg: running
                          ? "blue.50"
                          : queued
                            ? "purple.50"
                            : "gray.50",
                      }}
                    >
                      <Flex
                        alignItems="center"
                        justifyContent="space-between"
                        gap={2}
                        w="100%"
                        minW={0}
                      >
                        <Flex alignItems="center" gap={2} minW={0} flex="1">
                          <FlowStatusDot paused={paused} />
                          <Text
                            truncate
                            fontSize="sm"
                            fontWeight={isSelected ? "medium" : "normal"}
                            color="gray.800"
                          >
                            {p.name}
                          </Text>
                        </Flex>
                        <PipelineRightStatus pipeline={p} compact />
                      </Flex>
                    </Menu.Item>
                  );
                })}
              </Fragment>
            ))}
          </Menu.Content>
        </Menu.Positioner>
      </Portal>
    </Menu.Root>
  );
};

export default PipelinePicker;
