import { useEffect, useState } from "react";

import { Box, Button, Checkbox, Text } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import { useUpdatePipelineNode } from "@/queryOptions/pipeline/usePipeline";
import { type PipelineNodeDto } from "@/types/pipeline";

type BatchNotificationPanelProps = {
  pipelineId: number;
  nodeId: number;
  pipelineNode?: PipelineNodeDto;
  disabled?: boolean;
  embedded?: boolean;
};

const BatchNotificationPanel = ({
  pipelineId,
  nodeId,
  pipelineNode,
  disabled = false,
  embedded = false,
}: BatchNotificationPanelProps) => {
  const updateNode = useUpdatePipelineNode(pipelineId);
  const currentValue = Boolean(pipelineNode?.notify_on_completion);
  const [notifyOnCompletion, setNotifyOnCompletion] = useState(currentValue);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync toggle when node notification setting changes
    setNotifyOnCompletion(Boolean(pipelineNode?.notify_on_completion));
  }, [pipelineNode?.notify_on_completion, nodeId]);

  const hasChanges = notifyOnCompletion !== currentValue;

  const handleSave = async () => {
    if (!hasChanges) return;
    try {
      await updateNode.mutateAsync({
        nodeId,
        payload: { notify_on_completion: notifyOnCompletion },
      });
      toaster.success({ title: "Batch notification setting saved" });
    } catch {
      toaster.error({ title: "Could not save batch notification setting" });
    }
  };

  return (
    <Box
      borderWidth={embedded ? 0 : 1}
      borderColor="gray.200"
      borderRadius={embedded ? 0 : "md"}
      p={embedded ? 0 : 3}
      bg={embedded ? "transparent" : "gray.50"}
    >
      {!embedded && (
        <>
          <Text fontSize="sm" fontWeight="semibold" mb={1}>
            Batch completion email
          </Text>
          <Text fontSize="xs" color="gray.600" mb={3}>
            Uses notification recipients configured on the Start node.
          </Text>
        </>
      )}
      {embedded && (
        <Text fontSize="xs" color="gray.600" mb={2}>
          Recipients are set on the Start node Email tab.
        </Text>
      )}
      <Checkbox.Root
        checked={notifyOnCompletion}
        onCheckedChange={({ checked }) => setNotifyOnCompletion(!!checked)}
        disabled={disabled}
        size="sm"
      >
        <Checkbox.HiddenInput />
        <Checkbox.Control />
        <Checkbox.Label fontSize="sm">
          Send email when this batch completes
        </Checkbox.Label>
      </Checkbox.Root>
      <Button
        size="sm"
        colorPalette="brand"
        mt={3}
        w="full"
        onClick={handleSave}
        loading={updateNode.isPending}
        disabled={disabled || !hasChanges}
      >
        Save
      </Button>
    </Box>
  );
};

export default BatchNotificationPanel;
