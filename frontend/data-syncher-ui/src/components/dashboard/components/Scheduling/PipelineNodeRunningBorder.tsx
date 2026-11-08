import { type ReactNode, useEffect, useRef, useState } from "react";

import { Box } from "@chakra-ui/react";

import {
  PIPELINE_BATCH_NODE_RADIUS_PX,
  PIPELINE_NODE,
} from "./pipelineNodeStyles";

/** Matches @xyflow/react animated edge dash (stroke-dasharray: 5, dashdraw). */
const RUNNING_STROKE = {
  fill: "none",
  stroke: PIPELINE_NODE.edgeActive,
  strokeWidth: 2,
  strokeDasharray: "5",
  vectorEffect: "non-scaling-stroke" as const,
  style: { animation: "dashdraw 0.5s linear infinite" },
};

const RUNNING_OUTLINE_INSET_PX = 2;

type PipelineNodeRunningBorderProps = {
  active: boolean;
  borderRadius?: number;
  children: ReactNode;
};

const PipelineNodeRunningBorder = ({
  active,
  borderRadius = PIPELINE_BATCH_NODE_RADIUS_PX,
  children,
}: PipelineNodeRunningBorderProps) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !active) {
      return;
    }

    const update = () => {
      // Use layout dimensions, not getBoundingClientRect(). The canvas viewport
      // applies a zoom transform; screen-space rects inflate the SVG outline.
      setSize({
        w: Math.round(el.offsetWidth),
        h: Math.round(el.offsetHeight),
      });
    };

    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [active]);

  const effectiveSize = active ? size : { w: 0, h: 0 };
  const outlineW = effectiveSize.w + RUNNING_OUTLINE_INSET_PX * 2;
  const outlineH = effectiveSize.h + RUNNING_OUTLINE_INSET_PX * 2;

  return (
    <Box
      ref={wrapRef}
      position="relative"
      display="inline-block"
      w="fit-content"
      h="fit-content"
    >
      {children}
      {active && size.w > 0 && size.h > 0 && (
        <svg
          aria-hidden
          style={{
            position: "absolute",
            top: -RUNNING_OUTLINE_INSET_PX,
            left: -RUNNING_OUTLINE_INSET_PX,
            width: outlineW,
            height: outlineH,
            pointerEvents: "none",
            overflow: "visible",
          }}
          width={outlineW}
          height={outlineH}
          viewBox={`0 0 ${outlineW} ${outlineH}`}
        >
          <rect
            x={1}
            y={1}
            width={outlineW - 2}
            height={outlineH - 2}
            rx={borderRadius}
            ry={borderRadius}
            {...RUNNING_STROKE}
          />
        </svg>
      )}
    </Box>
  );
};

export default PipelineNodeRunningBorder;
