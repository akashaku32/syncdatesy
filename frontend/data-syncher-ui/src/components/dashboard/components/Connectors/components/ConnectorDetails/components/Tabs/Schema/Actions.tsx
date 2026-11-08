import { useEffect, useMemo } from "react";

import { Button, Flex, Text } from "@chakra-ui/react";

import { MdRefresh } from "react-icons/md";

import { useOutletContext } from "react-router";

import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import useUpdateSchema from "@/queryOptions/connector/schema/useUpdateSchema";
import useUpdateSchemaStatus from "@/queryOptions/connector/schema/useUpdateSchemaStatus";
import { type Connector } from "@/types/connectors";

import { useIsMutating } from "@tanstack/react-query";

const Actions = ({
  shouldShowDisabledState,
  setShouldShowDisabledState,
  onUpdateSchemaComplete,
  reloadingTables,
  updateSchemaLabel,
  updateSchemaIcon,
  onUpdateSchemaClick,
  leftChild,
  updateSchemaPosition = "right",
}: {
  shouldShowDisabledState: boolean;
  setShouldShowDisabledState: (_value: boolean) => void;
  onUpdateSchemaComplete?: () => void;
  reloadingTables: string[];
  updateSchemaLabel?: string;
  updateSchemaIcon?: React.ReactNode;
  onUpdateSchemaClick?: () => void;
  leftChild?: React.ReactNode;
  updateSchemaPosition?: "left" | "right";
}) => {
  const context = useOutletContext<Connector>();
  const {
    connection_id,
    target_database,
    target_schema,
    disable_update_schema,
  } = context;

  const isODataSchemaActions = Boolean(leftChild || onUpdateSchemaClick);

  const { mutate: updateSchema, isPending: isUpdating } = useUpdateSchema({
    connectorId: connection_id,
  });

  const isRefreshSchemaInProgress = useIsMutating({
    mutationKey: ["refreshSchema", connection_id],
  });

  const isUpdateSchemaInProgress = useIsMutating({
    mutationKey: ["updateSchema", connection_id],
  });

  const { data: schemaStatus } = useUpdateSchemaStatus(connection_id, true);

  const isRefreshDeltaTableInProgress = useIsMutating({
    mutationKey: ["refreshDeltaTable", connection_id],
  });

  const isReloadSingleTableInProgress = useIsMutating({
    mutationKey: ["reloadSingleTable", connection_id],
  });

  const { data: tableStatusData } = useFetchTableStatus(connection_id, true);

  const hasAnyTableInProgress = useMemo(() => {
    const hasTableInProgress =
      tableStatusData?.tables?.some(
        (table) => table.status === "in_progress",
      ) ?? false;
    const isDeltaTableRefreshing = isRefreshDeltaTableInProgress > 0;
    const isReloading =
      isReloadSingleTableInProgress > 0 ||
      (reloadingTables && reloadingTables.length > 0);

    return hasTableInProgress || isDeltaTableRefreshing || isReloading;
  }, [
    tableStatusData,
    isRefreshDeltaTableInProgress,
    isReloadSingleTableInProgress,
    reloadingTables,
  ]);

  const isAnyOperationInProgress =
    isRefreshSchemaInProgress > 0 ||
    isUpdateSchemaInProgress > 0 ||
    isUpdating ||
    schemaStatus?.is_in_progress === true ||
    tableStatusData?.schema_refresh_in_progress === true ||
    hasAnyTableInProgress;

  const isUpdateSchemaFlowInProgress =
    isUpdating || (schemaStatus?.is_in_progress ?? false);

  useEffect(() => {
    if (
      !isAnyOperationInProgress &&
      shouldShowDisabledState &&
      !isUpdating &&
      isUpdateSchemaInProgress === 0 &&
      isRefreshSchemaInProgress === 0
    ) {
      const timer = setTimeout(() => {
        setShouldShowDisabledState(false);
        onUpdateSchemaComplete?.();
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [
    isAnyOperationInProgress,
    shouldShowDisabledState,
    isUpdating,
    isUpdateSchemaInProgress,
    isRefreshSchemaInProgress,
    onUpdateSchemaComplete,
    setShouldShowDisabledState,
  ]);

  const createTooltipProps = (isButtonLoading: boolean) => {
    const isDisabled =
      (shouldShowDisabledState || isAnyOperationInProgress) && !isButtonLoading;
    return {
      content: isDisabled
        ? "Another migration is currently in progress. Please wait until it completes."
        : "",
      disabled:
        (!shouldShowDisabledState && !isAnyOperationInProgress) ||
        isButtonLoading,
    };
  };

  const renderUpdateSchemaButton = () => {
    if (disable_update_schema) return null;
    return (
      <Tooltip {...createTooltipProps(isUpdateSchemaFlowInProgress)}>
        <Button
          variant="outline"
          colorPalette="brand"
          loading={isUpdateSchemaFlowInProgress}
          disabled={
            (shouldShowDisabledState || isAnyOperationInProgress) &&
            !isUpdateSchemaFlowInProgress
          }
          onClick={() => {
            if (onUpdateSchemaClick) {
              onUpdateSchemaClick();
              return;
            }
            if (
              (shouldShowDisabledState || isAnyOperationInProgress) &&
              !isUpdateSchemaFlowInProgress
            ) {
              toaster.warning({
                title: "Operation in progress",
                description:
                  "Another migration is currently in progress. Please wait until it completes.",
              });
              return;
            }
            setShouldShowDisabledState(true);
            updateSchema(undefined, {
              onError: () => {
                setShouldShowDisabledState(false);
              },
            });
          }}
        >
          {updateSchemaIcon !== undefined ? updateSchemaIcon : <MdRefresh />}
          {updateSchemaLabel || "Update schema"}
        </Button>
      </Tooltip>
    );
  };

  if (isODataSchemaActions) {
    return (
      <Flex
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={4}
        mb={2}
        minW="xl"
        w="100%"
      >
        <Flex gap={4} alignItems="center" flexWrap="wrap">
          {leftChild}
          {updateSchemaPosition === "left" && renderUpdateSchemaButton()}
        </Flex>

        <Flex gap={4}>
          {updateSchemaPosition !== "left" && renderUpdateSchemaButton()}
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex
      justifyContent="space-between"
      alignItems="center"
      flexWrap="wrap"
      gap={4}
      mb={2}
      minW="xl"
    >
      <Flex direction="column" gap={2}>
        <Text fontWeight="semibold">Destination Details</Text>
        <Flex gap={12} alignItems="center" flexWrap="wrap">
          <Flex gap={2}>
            <Text color="gray.600">Database:</Text>
            <Text fontWeight="semibold">{target_database}</Text>
          </Flex>
          <Flex gap={2}>
            <Text color="gray.600">Schema:</Text>
            <Text fontWeight="semibold">{target_schema}</Text>
          </Flex>
        </Flex>
      </Flex>

      <Flex gap={4}>{renderUpdateSchemaButton()}</Flex>
    </Flex>
  );
};

export default Actions;
