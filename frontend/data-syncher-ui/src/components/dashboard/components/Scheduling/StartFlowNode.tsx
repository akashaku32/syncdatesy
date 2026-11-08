import { memo } from "react";

import { Box, Flex, Text } from "@chakra-ui/react";

import { MdPlayArrow } from "react-icons/md";

import { PIPELINE_NODE } from "./pipelineNodeStyles";
import { Handle, type NodeProps, Position } from "@xyflow/react";

export type StartFlowNodeData = {
  selected?: boolean;
  nextSyncLabel?: string | null;
};

const StartFlowNode = ({ data }: NodeProps) => {
  const nodeData = data as StartFlowNodeData;
  const borderColor = nodeData.selected
    ? PIPELINE_NODE.borderSelected
    : PIPELINE_NODE.borderRoot;

  return (
    <>
      <Flex direction="column" alignItems="center" gap={1}>
        <Box
          borderWidth={2}
          borderRadius="full"
          p={3}
          minW="72px"
          minH="72px"
          display="flex"
          alignItems="center"
          justifyContent="center"
          bg={PIPELINE_NODE.bgRoot}
          borderColor={borderColor}
          boxShadow={
            nodeData.selected
              ? "0 0 0 2px var(--chakra-colors-brand-200)"
              : "sm"
          }
          cursor="pointer"
        >
          <Flex direction="column" alignItems="center" gap={1}>
            <MdPlayArrow size={22} color="var(--chakra-colors-brand-600)" />
            <Text fontSize="xs" fontWeight="bold" color="brand.700">
              Start
            </Text>
          </Flex>
        </Box>
        {nodeData.nextSyncLabel && (
          <Text
            fontSize="2xs"
            color="gray.600"
            fontWeight="medium"
            textAlign="center"
            maxW="120px"
            lineHeight="short"
          >
            {nodeData.nextSyncLabel}
          </Text>
        )}
      </Flex>
      <Handle
        type="source"
        position={Position.Right}
        id="right"
        style={{
          background: PIPELINE_NODE.handle,
          width: 8,
          height: 8,
          border: "2px solid white",
        }}
      />
    </>
  );
};

export default memo(StartFlowNode);
