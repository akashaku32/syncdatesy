import { memo } from "react";

import { Badge, Box, Flex, IconButton, Text } from "@chakra-ui/react";

import { MdClose } from "react-icons/md";

import PipelineNodeRunningBorder from "./PipelineNodeRunningBorder";
import {
  PIPELINE_NODE,
  type PipelineNodeRunVisualStatus,
  pipelineNodeChrome,
} from "./pipelineNodeStyles";
import { Handle, type NodeProps, Position } from "@xyflow/react";

export type BatchFlowNodeData = {
  batchId: number;
  connectionId: number;
  batchName: string;
  connectionName: string;
  tableCount: number;
  executionOrder: "parallel" | "sequential";
  selected?: boolean;
  isRoot?: boolean;
  parentBatchName?: string | null;
  runStatus?: PipelineNodeRunVisualStatus;
  tablesCompleted?: number;
  tablesTotal?: number;
  /** Restarts the repeating progress animation when a new run starts. */
  runFillKey?: number | string;
  isDraft?: boolean;
  onDelete?: (_nodeId: string) => void;
};

const BatchFlowNode = ({ id, data }: NodeProps) => {
  const nodeData = data as BatchFlowNodeData;
  const chrome = pipelineNodeChrome({
    selected: nodeData.selected,
    runStatus: nodeData.runStatus,
    isDraft: nodeData.isDraft,
  });
  const isRunning = nodeData.runStatus === "running";

  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        id="left"
        style={{
          background: PIPELINE_NODE.handle,
          width: 7,
          height: 7,
          border: "2px solid white",
        }}
      />
      <PipelineNodeRunningBorder active={isRunning}>
        <Box
          borderWidth={isRunning ? 0 : 1}
          borderRadius="xl"
          p={2}
          minW="148px"
          maxW="176px"
          cursor="pointer"
          position="relative"
          overflow="hidden"
          transition="border-color 0.2s, box-shadow 0.2s, background-color 0.3s"
          bg={isRunning ? "white" : chrome.bg}
          borderColor={chrome.borderColor}
          borderStyle={chrome.borderStyle}
          boxShadow={chrome.boxShadow}
        >
          {isRunning && (
            <Box
              key={nodeData.runFillKey ?? id}
              aria-hidden
              className="pipeline-node-color-progress"
              position="absolute"
              inset={0}
              bg="blue.200"
              pointerEvents="none"
            />
          )}
          <Box position="relative" zIndex={1}>
            <Flex
              justifyContent="space-between"
              alignItems="flex-start"
              gap={1}
            >
              <Box flex="1" minW={0}>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color={PIPELINE_NODE.text}
                  truncate
                  title={nodeData.batchName}
                  lineHeight="short"
                >
                  {nodeData.batchName}
                </Text>
                <Text
                  fontSize="2xs"
                  color={PIPELINE_NODE.textMuted}
                  truncate
                  title={nodeData.connectionName}
                >
                  {nodeData.connectionName}
                </Text>
              </Box>
              {nodeData.onDelete && (
                <IconButton
                  aria-label="Remove batch from pipeline"
                  size="2xs"
                  variant="ghost"
                  colorPalette="gray"
                  minW={5}
                  h={5}
                  onClick={(e) => {
                    e.stopPropagation();
                    nodeData.onDelete?.(id);
                  }}
                >
                  <MdClose size={12} />
                </IconButton>
              )}
            </Flex>
            <Flex mt={1.5} gap={1} alignItems="center" flexWrap="wrap">
              {nodeData.isDraft && (
                <Badge
                  size="sm"
                  colorPalette="orange"
                  variant="subtle"
                  fontSize="2xs"
                  px={1.5}
                >
                  Draft
                </Badge>
              )}
              <Badge
                size="sm"
                colorPalette="gray"
                variant="subtle"
                fontSize="2xs"
              >
                {nodeData.tableCount} tbl
              </Badge>
              {nodeData.runStatus && nodeData.runStatus !== "pending" && (
                <Badge
                  size="sm"
                  colorPalette={
                    nodeData.runStatus === "completed"
                      ? "green"
                      : nodeData.runStatus === "failed"
                        ? "red"
                        : nodeData.runStatus === "waiting"
                          ? "gray"
                          : nodeData.runStatus === "skipped"
                            ? "orange"
                            : "blue"
                  }
                  variant={
                    nodeData.runStatus === "waiting" ||
                    nodeData.runStatus === "skipped"
                      ? "outline"
                      : "subtle"
                  }
                  fontSize="2xs"
                >
                  {nodeData.runStatus === "running" &&
                  nodeData.tablesTotal &&
                  nodeData.tablesTotal > 0
                    ? `${nodeData.tablesCompleted ?? 0}/${nodeData.tablesTotal}`
                    : nodeData.runStatus}
                </Badge>
              )}
            </Flex>
          </Box>
        </Box>
      </PipelineNodeRunningBorder>
      <Handle
        type="source"
        position={Position.Right}
        id="right"
        style={{
          background: PIPELINE_NODE.handle,
          width: 7,
          height: 7,
          border: "2px solid white",
        }}
      />
    </>
  );
};

export default memo(BatchFlowNode);
