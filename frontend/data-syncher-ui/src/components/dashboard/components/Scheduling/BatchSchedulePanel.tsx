import { useEffect, useState } from "react";

import { Box, Button, Flex, Switch, Text } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import {
  pipelinesQueryKey,
  usePatchPipeline,
  useValidatePipeline,
} from "@/queryOptions/pipeline/usePipeline";
import {
  type PipelineDetail,
  type PipelineValidationResult,
} from "@/types/pipeline";

import ScheduleEditor from "./ScheduleEditor";
import {
  type ScheduleValue,
  fromPipelineSchedule,
  pipelineScheduleLabel,
  toApiSchedule,
  weeklySelectionValid,
} from "./scheduleOptions";
import { useQueryClient } from "@tanstack/react-query";

type BatchSchedulePanelProps = {
  pipeline: PipelineDetail;
  disabled?: boolean;
  embedded?: boolean;
  onValidationResult?: (_result: PipelineValidationResult) => void;
};

const BatchSchedulePanel = ({
  pipeline,
  disabled = false,
  embedded = false,
  onValidationResult,
}: BatchSchedulePanelProps) => {
  const queryClient = useQueryClient();
  const patchPipeline = usePatchPipeline(pipeline.id);
  const validatePipeline = useValidatePipeline();
  const [isTogglingSchedulePause, setIsTogglingSchedulePause] = useState(false);
  const [isSavingSchedule, setIsSavingSchedule] = useState(false);

  const [scheduleDraft, setScheduleDraft] = useState<ScheduleValue>(() =>
    fromPipelineSchedule(pipeline),
  );

  const scheduleSummary = pipelineScheduleLabel(pipeline);
  const schedulePaused = Boolean(pipeline.schedule_paused);
  const scheduleActive = !schedulePaused;

  useEffect(() => {
    setScheduleDraft(fromPipelineSchedule(pipeline));
  }, [pipeline]);

  const publishDraftForSchedule = async (): Promise<boolean> => {
    const result = await validatePipeline.mutateAsync(pipeline.id);
    onValidationResult?.(result);
    if (!result.valid) {
      toaster.error({
        title: "Fix the draft flow before saving the schedule",
        description: `${result.errors.length} issue${result.errors.length === 1 ? "" : "s"} found. See validation details on the Flow tab.`,
      });
      return false;
    }
    await queryClient.invalidateQueries({ queryKey: pipelinesQueryKey });
    return true;
  };

  const handleSaveSchedule = async () => {
    if (!weeklySelectionValid(scheduleDraft)) {
      toaster.error({ title: "Select at least one day for weekly schedule" });
      return;
    }
    setIsSavingSchedule(true);
    try {
      if (!(await publishDraftForSchedule())) {
        return;
      }
      const apiSchedule = toApiSchedule(scheduleDraft);
      await patchPipeline.mutateAsync({
        ...apiSchedule,
        sync_start_date: scheduleDraft.sync_start_date,
        sync_end_date:
          scheduleDraft.end_mode === "on_date"
            ? scheduleDraft.sync_end_date
            : null,
      });
      toaster.success({
        title: "Pipeline schedule saved",
        description: "Draft flow validated and published for scheduled runs.",
      });
    } catch {
      toaster.error({ title: "Could not save schedule" });
    } finally {
      setIsSavingSchedule(false);
    }
  };

  const handleScheduleActiveChange = async (active: boolean) => {
    const nextPaused = !active;
    if (nextPaused === schedulePaused) return;

    setIsTogglingSchedulePause(true);
    try {
      if (!nextPaused && !(await publishDraftForSchedule())) {
        return;
      }
      await patchPipeline.mutateAsync({ schedule_paused: nextPaused });
      toaster.success({
        title: nextPaused ? "Schedule paused" : "Schedule resumed",
      });
    } catch {
      toaster.error({ title: "Failed to update schedule status" });
    } finally {
      setIsTogglingSchedulePause(false);
    }
  };

  return (
    <Box>
      <Box
        borderWidth={1}
        borderColor={schedulePaused ? "orange.200" : "gray.200"}
        borderRadius="md"
        p={3}
        mb={embedded ? 3 : 4}
        bg={schedulePaused ? "orange.50" : "gray.50"}
      >
        <Flex align="center" justify="space-between" gap={3}>
          <Box flex="1" minW={0}>
            <Text fontSize="xs" fontWeight="semibold" color="gray.800">
              Automatic schedule
            </Text>
            <Text
              fontSize="2xs"
              color={schedulePaused ? "orange.700" : "gray.500"}
              mt={0.5}
            >
              {schedulePaused
                ? "Paused — scheduled runs are stopped"
                : "Active — runs on the configured cadence"}
            </Text>
            {scheduleSummary && (
              <Text
                fontSize="2xs"
                color="brand.700"
                fontWeight="medium"
                mt={1.5}
                truncate
                title={scheduleSummary}
              >
                {scheduleSummary}
              </Text>
            )}
          </Box>
          <Flex align="center" gap={2} flexShrink={0}>
            <Text
              fontSize="2xs"
              fontWeight="medium"
              color={scheduleActive ? "brand.600" : "orange.600"}
              minW="42px"
              textAlign="right"
            >
              {scheduleActive ? "Active" : "Paused"}
            </Text>
            <Switch.Root
              checked={scheduleActive}
              onCheckedChange={({ checked }) =>
                void handleScheduleActiveChange(!!checked)
              }
              disabled={isTogglingSchedulePause}
              colorPalette="brand"
              aria-label="Toggle automatic schedule"
            >
              <Switch.HiddenInput />
              <Switch.Control />
            </Switch.Root>
          </Flex>
        </Flex>
      </Box>

      {!embedded && (
        <>
          <Text
            fontSize="2xs"
            color="gray.500"
            textTransform="uppercase"
            mb={1}
          >
            Configure schedule
          </Text>
          <Text fontSize="xs" color="gray.600" mb={3}>
            Set when this pipeline should run automatically.
          </Text>
        </>
      )}

      <ScheduleEditor
        value={scheduleDraft}
        onChange={setScheduleDraft}
        disabled={disabled}
        showExecutionMode={false}
      />
      <Button
        size="sm"
        colorPalette="brand"
        mt={embedded ? 3 : 4}
        w="full"
        onClick={handleSaveSchedule}
        loading={
          isSavingSchedule ||
          validatePipeline.isPending ||
          (patchPipeline.isPending && !isTogglingSchedulePause)
        }
        disabled={disabled}
      >
        Save schedule
      </Button>
    </Box>
  );
};

export default BatchSchedulePanel;
