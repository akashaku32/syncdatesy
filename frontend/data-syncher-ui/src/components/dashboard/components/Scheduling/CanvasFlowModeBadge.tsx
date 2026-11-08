import { Badge } from "@chakra-ui/react";

import type { PipelineRunMode } from "@/types/pipeline";

import {
  pipelineRunModeColor,
  pipelineRunModeLabel,
} from "./pipelineRunHelpers";
import { Panel } from "@xyflow/react";

type CanvasFlowModeBadgeProps = {
  mode: PipelineRunMode;
};

const CanvasFlowModeBadge = ({ mode }: CanvasFlowModeBadgeProps) => (
  <Panel position="top-center" style={{ marginTop: 8, pointerEvents: "none" }}>
    <Badge
      colorPalette={pipelineRunModeColor(mode)}
      size="sm"
      variant="subtle"
      px={3}
      py={1}
      fontWeight="medium"
      boxShadow="md"
      borderWidth={1}
      borderColor="gray.200"
      bg="white"
    >
      {pipelineRunModeLabel(mode)}
    </Badge>
  </Panel>
);

export default CanvasFlowModeBadge;
