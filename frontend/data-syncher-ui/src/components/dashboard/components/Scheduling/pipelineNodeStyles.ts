/** Shared classic styling for pipeline canvas nodes and sidebar batch chips. */
export const PIPELINE_NODE = {
  bg: "white",
  bgRoot: "brand.50",
  /** Soft pastel fills for run progress (readable, not dark). */
  bgRunning: "blue.50",
  bgCompleted: "green.50",
  bgFailed: "red.50",
  border: "gray.200",
  borderRoot: "brand.200",
  borderSelected: "brand.500",
  borderRunning: "blue.300",
  borderCompleted: "green.300",
  borderFailed: "red.300",
  text: "gray.800",
  textMuted: "gray.500",
  handle: "#7C3AED",
  edge: "#A78BFA",
  /** Mid blue for in-progress edges (readable against the canvas). */
  edgeActive: "#3B82F6",
} as const;

export type PipelineNodeRunVisualStatus =
  | "pending"
  | "waiting"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export function pipelineNodeChrome(options: {
  selected?: boolean;
  runStatus?: PipelineNodeRunVisualStatus;
  isDraft?: boolean;
}) {
  const { selected, runStatus, isDraft } = options;

  let bg: string = PIPELINE_NODE.bg;
  if (runStatus === "completed") {
    bg = PIPELINE_NODE.bgCompleted;
  } else if (runStatus === "failed") {
    bg = PIPELINE_NODE.bgFailed;
  }

  let borderColor: string = PIPELINE_NODE.border;
  if (selected) {
    borderColor = PIPELINE_NODE.borderSelected;
  } else if (runStatus === "running") {
    // Animated dashed outline is drawn by PipelineNodeRunningBorder.
    borderColor = "transparent";
  } else if (runStatus === "completed") {
    borderColor = PIPELINE_NODE.borderCompleted;
  } else if (runStatus === "failed") {
    borderColor = PIPELINE_NODE.borderFailed;
  } else if (runStatus === "waiting") {
    borderColor = "gray.300";
  } else if (runStatus === "skipped") {
    borderColor = "orange.200";
  } else if (isDraft) {
    borderColor = "orange.300";
  }

  return {
    bg,
    borderColor,
    borderStyle: isDraft || runStatus === "skipped" ? "dashed" : "solid",
    boxShadow:
      selected && runStatus !== "running"
        ? "0 0 0 1px var(--chakra-colors-brand-500)"
        : runStatus === "running"
          ? "none"
          : "sm",
  };
}

/** Chakra `borderRadius="xl"` — keep running SVG outline aligned with batch nodes. */
export const PIPELINE_BATCH_NODE_RADIUS_PX = 12;
