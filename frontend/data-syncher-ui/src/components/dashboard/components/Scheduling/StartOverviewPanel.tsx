import { useState } from "react";

import { Box, Flex, IconButton, Tabs, Text } from "@chakra-ui/react";

import { MdClose } from "react-icons/md";

import {
  type PipelineDetail,
  type PipelineValidationResult,
} from "@/types/pipeline";

import BatchSchedulePanel from "./BatchSchedulePanel";
import PipelineNotificationPanel from "./PipelineNotificationPanel";
import {
  pipelineScheduleLabel,
  startNodeScheduleLabels,
} from "./scheduleOptions";

type StartOverviewPanelProps = {
  pipeline: PipelineDetail;
  onClose: () => void;
  onValidationResult?: (_result: PipelineValidationResult) => void;
};

const StartOverviewPanel = ({
  pipeline,
  onClose,
  onValidationResult,
}: StartOverviewPanelProps) => {
  const scheduleSummary = pipelineScheduleLabel(pipeline);
  const { nextSyncLabel } = startNodeScheduleLabels(pipeline);
  const pipelinePaused = pipeline.status === "paused";
  const [activeTab, setActiveTab] = useState("schedule");

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
        borderColor="gray.100"
        flexShrink={0}
      >
        <Box>
          <Text fontSize="sm" fontWeight="semibold">
            Start
          </Text>
          <Text fontSize="xs" color="gray.500">
            Pipeline entry point
            {nextSyncLabel ? ` · ${nextSyncLabel}` : ""}
          </Text>
        </Box>
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
          <Tabs.Trigger value="schedule" fontSize="xs" py={2} px={3}>
            Schedule
          </Tabs.Trigger>
          <Tabs.Trigger value="email" fontSize="xs" py={2} px={3}>
            Email
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="schedule" flex="1" overflowY="auto" p={3} minH={0}>
          <Text fontSize="xs" color="gray.600" mb={2}>
            Scheduler and Run now begin here.
            {scheduleSummary
              ? ` Currently ${scheduleSummary.toLowerCase()}.`
              : ""}
          </Text>
          <BatchSchedulePanel
            pipeline={pipeline}
            disabled={pipelinePaused}
            embedded
            onValidationResult={onValidationResult}
          />
        </Tabs.Content>

        <Tabs.Content value="email" flex="1" overflowY="auto" p={3} minH={0}>
          <PipelineNotificationPanel
            pipeline={pipeline}
            disabled={pipelinePaused}
            embedded
          />
        </Tabs.Content>
      </Tabs.Root>
    </Flex>
  );
};

export default StartOverviewPanel;
