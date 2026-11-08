import { type MouseEvent } from "react";

import { Badge, Box, Flex, Image, Text } from "@chakra-ui/react";

import {
  FaExclamationTriangle,
  FaPauseCircle,
  FaPlayCircle,
} from "react-icons/fa";

import { format } from "date-fns";
import { useNavigate } from "react-router";

import CheckIcon from "@/assets/icons/check-icon.svg";
import ErrorIcon from "@/assets/icons/error-icon.svg";
import SandtimeIcon from "@/assets/icons/sand-time-icon.svg";
import ClientRoutes from "@/constants/client-routes";
import { dateTimeFormat } from "@/constants/common";
import { getUiState } from "@/helpers/log";
import { type ConnectorActivityLog } from "@/types/connectors";

const Item = ({
  log,
  onClick,
  pointerEvent,
  selectedLog,
}: {
  log: ConnectorActivityLog;
  onClick: () => void;
  pointerEvent: "pointer" | "not-allowed";
  selectedLog: number | null;
}) => {
  const navigate = useNavigate();
  const {
    message,
    user,
    user_name,
    timestamp,
    session_id,
    migration_id,
    ui_state,
    trigger_type,
    status,
    batch_name,
    batch_names,
    pipeline_id,
    pipeline_name,
    pipeline_run_id,
  } = log;

  // Use the helper to determine the UI state if not provided
  const derivedUiState = getUiState(ui_state, status, message);

  // Prioritize log_id for uniqueness, fallback to migration_id or session_id
  const idToCompare = log.log_id ?? migration_id ?? session_id;
  const isSelected = idToCompare && idToCompare === selectedLog;

  const displayUser = user_name || user;
  const batchLabel =
    batch_name ||
    (batch_names && batch_names.length > 0 ? batch_names.join(", ") : null);

  const openPipelineExecutionLogs = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!pipeline_id && !pipeline_name) return;

    const params = new URLSearchParams();
    if (pipeline_id !== null && pipeline_id !== undefined) {
      params.set("pipeline", String(pipeline_id));
    } else if (pipeline_name) {
      params.set("pipelineName", pipeline_name);
    }
    params.set("tab", "logs");
    if (pipeline_run_id !== null && pipeline_run_id !== undefined) {
      params.set("run", String(pipeline_run_id));
    }
    const processName = batch_name ?? batch_names?.[0] ?? null;
    if (processName) {
      params.set("process", processName);
    }
    navigate(
      `${ClientRoutes.DASHBOARD}/${ClientRoutes.SCHEDULING}?${params.toString()}`,
    );
  };

  return (
    <Flex
      gap={2}
      py={1}
      px={2}
      borderBottom="1px solid #E2E8F0"
      bg={isSelected ? "blue.50" : "white"}
      _hover={{ bg: isSelected ? "blue.100" : "gray.50" }}
      cursor={pointerEvent}
      onClick={onClick}
      borderRadius="md"
      transition="background-color 0.2s"
    >
      <Box pt={0.5}>
        {derivedUiState === "success" && (
          <Image src={CheckIcon} w="16px" h="16px" objectFit="contain" />
        )}
        {derivedUiState === "in_progress" && (
          <Image src={SandtimeIcon} w="16px" h="16px" objectFit="contain" />
        )}
        {derivedUiState === "paused" && (
          <FaPauseCircle color="#DD6B20" size={16} />
        )}
        {derivedUiState === "active" && (
          <FaPlayCircle color="#38A169" size={16} />
        )}
        {derivedUiState === "error" && (
          <Image src={ErrorIcon} w="16px" h="16px" objectFit="contain" />
        )}
        {derivedUiState === "warning" && (
          <FaExclamationTriangle color="#DD6B20" size={16} />
        )}
      </Box>
      <Flex direction="column" flex={1} gap={0.5}>
        <Text
          fontSize="sm"
          fontWeight="medium"
          color="gray.700"
          lineHeight="short"
          lineClamp={2}
        >
          {message}
        </Text>
        {(batchLabel || pipeline_name) && (
          <Flex gap={1.5} flexWrap="wrap" alignItems="center">
            {batchLabel && (
              <Badge
                colorPalette="blue"
                variant="subtle"
                size="sm"
                borderRadius="md"
              >
                Batch: {batchLabel}
              </Badge>
            )}
            {pipeline_name && (
              <Badge
                as="button"
                colorPalette="purple"
                variant="outline"
                size="sm"
                borderRadius="md"
                cursor="pointer"
                title="Open pipeline execution logs"
                onClick={openPipelineExecutionLogs}
                _hover={{ bg: "purple.50" }}
              >
                Pipeline: {pipeline_name}
                {pipeline_run_id ? ` #${pipeline_run_id}` : ""}
              </Badge>
            )}
          </Flex>
        )}
        <Flex gap={4} alignItems="center" flexWrap="wrap">
          <Text fontSize="xs" color="gray.500" whiteSpace="nowrap">
            {timestamp ? format(new Date(timestamp), dateTimeFormat) : ""}
          </Text>
          <Text fontSize="xs" color="gray.500">
            {displayUser}
          </Text>
          {trigger_type && (
            <Text fontSize="xs" color="gray.500">
              {trigger_type}
            </Text>
          )}
        </Flex>
      </Flex>
    </Flex>
  );
};

export default Item;
