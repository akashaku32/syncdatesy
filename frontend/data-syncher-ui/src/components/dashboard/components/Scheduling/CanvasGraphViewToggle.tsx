import { Flex, IconButton } from "@chakra-ui/react";

import { MdCheckCircle, MdEdit, MdLock } from "react-icons/md";

import { Tooltip } from "@/components/ui/tooltip";

import { Panel } from "@xyflow/react";

export type CanvasGraphView = "draft" | "published";

type CanvasGraphViewToggleProps = {
  value: CanvasGraphView;
  onChange: (_view: CanvasGraphView) => void;
  /** Show only when a published graph exists and the draft canvas differs from it. */
  visible: boolean;
};

const CanvasGraphViewToggle = ({
  value,
  onChange,
  visible,
}: CanvasGraphViewToggleProps) => {
  if (!visible) return null;

  return (
    <Panel position="top-right" style={{ marginTop: 12, marginRight: 12 }}>
      <Flex
        direction="column"
        gap={0.5}
        p={1}
        bg="white"
        borderWidth={1}
        borderColor="gray.200"
        borderRadius="md"
        boxShadow="sm"
        alignItems="center"
      >
        <Tooltip
          content="Draft canvas"
          positioning={{ placement: "left" }}
          showArrow
        >
          <IconButton
            aria-label="Draft canvas"
            size="xs"
            variant={value === "draft" ? "solid" : "ghost"}
            colorPalette="orange"
            bg={value === "draft" ? "orange.500" : undefined}
            color={value === "draft" ? "white" : "orange.700"}
            _hover={{
              bg: value === "draft" ? "orange.600" : "orange.50",
            }}
            onClick={() => onChange("draft")}
          >
            <MdEdit />
          </IconButton>
        </Tooltip>
        <Tooltip
          content={
            value === "published"
              ? "Published flow (read-only)"
              : "Published flow"
          }
          positioning={{ placement: "left" }}
          showArrow
        >
          <IconButton
            aria-label="Published flow"
            size="xs"
            variant={value === "published" ? "solid" : "ghost"}
            colorPalette="green"
            onClick={() => onChange("published")}
            position="relative"
          >
            <MdCheckCircle />
            {value === "published" && (
              <MdLock
                size={8}
                style={{
                  position: "absolute",
                  right: 2,
                  bottom: 2,
                }}
              />
            )}
          </IconButton>
        </Tooltip>
      </Flex>
    </Panel>
  );
};

export default CanvasGraphViewToggle;
