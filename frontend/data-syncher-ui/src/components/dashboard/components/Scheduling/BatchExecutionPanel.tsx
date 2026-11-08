import { useEffect, useState } from "react";

import { Box, Button, Flex, RadioGroup, Text } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import { useUpdatePipelineNode } from "@/queryOptions/pipeline/usePipeline";
import { type PipelineNodeDto } from "@/types/pipeline";

type BatchExecutionPanelProps = {
  pipelineId: number;
  nodeId: number;
  pipelineNode?: PipelineNodeDto;
  disabled?: boolean;
  embedded?: boolean;
};

const BatchExecutionPanel = ({
  pipelineId,
  nodeId,
  pipelineNode,
  disabled = false,
  embedded = false,
}: BatchExecutionPanelProps) => {
  const updateNode = useUpdatePipelineNode(pipelineId);
  const currentOrder =
    pipelineNode?.execution_order === "sequential" ? "sequential" : "parallel";

  const [executionOrder, setExecutionOrder] = useState<
    "parallel" | "sequential"
  >(currentOrder);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync order when node execution order changes
    setExecutionOrder(currentOrder);
  }, [currentOrder, nodeId]);

  const hasChanges = executionOrder !== currentOrder;

  const handleSave = async () => {
    if (!hasChanges) return;
    try {
      await updateNode.mutateAsync({
        nodeId,
        payload: { execution_order: executionOrder },
      });
      toaster.success({ title: "Execution mode saved" });
    } catch {
      toaster.error({ title: "Could not save execution mode" });
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
            Execution mode
          </Text>
          <Text fontSize="xs" color="gray.600" mb={3}>
            How tables inside this batch run when the pipeline reaches this
            task.
          </Text>
        </>
      )}
      {embedded && (
        <Text fontSize="xs" color="gray.600" mb={2}>
          How tables run when this batch is reached in the flow.
        </Text>
      )}
      <RadioGroup.Root
        value={executionOrder}
        onValueChange={({ value }) => {
          if (disabled || !value) return;
          setExecutionOrder(value as "parallel" | "sequential");
        }}
        disabled={disabled}
      >
        <Flex gap={4}>
          <RadioGroup.Item value="parallel">
            <RadioGroup.ItemHiddenInput />
            <RadioGroup.ItemIndicator />
            <RadioGroup.ItemText fontSize="sm">Parallel</RadioGroup.ItemText>
          </RadioGroup.Item>
          <RadioGroup.Item value="sequential">
            <RadioGroup.ItemHiddenInput />
            <RadioGroup.ItemIndicator />
            <RadioGroup.ItemText fontSize="sm">Sequential</RadioGroup.ItemText>
          </RadioGroup.Item>
        </Flex>
      </RadioGroup.Root>
      <Button
        size="sm"
        colorPalette="brand"
        mt={embedded ? 3 : 3}
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

export default BatchExecutionPanel;
