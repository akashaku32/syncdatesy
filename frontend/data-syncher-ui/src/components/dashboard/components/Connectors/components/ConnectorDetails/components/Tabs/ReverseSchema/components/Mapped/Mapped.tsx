import { forwardRef, useEffect, useImperativeHandle, useState } from "react";

import { Box, Flex, Text } from "@chakra-ui/react";

import { CiTrash } from "react-icons/ci";

import { useOutletContext } from "react-router";

import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ReverseSchemaResponse } from "@/queryOptions/connector/reverseSchema/useFetchReverseSchema";
import { type Connector } from "@/types/connectors";
import { type TableMapping, type TableMappingDTO } from "@/types/mappings";

import { validateTableToTableMapping } from "../../utils/validation";

export interface MappedRef {
  handleDrop: (_sourceTable: string, _destinationTable: string) => void;
  hasMapping: (_sourceTable: string, _destinationTable: string) => boolean;
  getMappings: () => TableMapping[];
  removeBySourceTable: (_sourceTable: string) => void;
}

interface MappedProps {
  reverseSchemaData: ReverseSchemaResponse | null;
  isDisabled?: boolean;
  onMappingsChange?: (_mappings: TableMapping[]) => void;
  /** When true, keep save/drop logic mounted but do not render the Mapped column. */
  hideUi?: boolean;
}

const normalizeMappings = (raw: unknown): TableMapping[] => {
  const list =
    (Array.isArray((raw as { mappings?: TableMappingDTO[] })?.mappings)
      ? (raw as { mappings: TableMappingDTO[] }).mappings
      : Array.isArray(raw)
        ? (raw as TableMappingDTO[])
        : Array.isArray((raw as { data?: TableMappingDTO[] })?.data)
          ? (raw as { data: TableMappingDTO[] }).data
          : []) ?? [];

  return list
    .map((item) => {
      const sourceTable =
        item.source_table ?? (item as { sourceTable?: string }).sourceTable;
      const destinationTable =
        item.destination_table ??
        (item as { destinationTable?: string }).destinationTable;
      if (!sourceTable || !destinationTable) return null;
      return { sourceTable, destinationTable, status: item.status };
    })
    .filter(Boolean) as TableMapping[];
};

const Mapped = forwardRef<MappedRef, MappedProps>((props, ref) => {
  const { reverseSchemaData, isDisabled, onMappingsChange, hideUi } = props;
  const context = useOutletContext<Connector>();
  const [mappings, setMappings] = useState<TableMapping[]>([]);

  useEffect(() => {
    const fetchMappings = async () => {
      try {
        const { data } = await AxiosInstance.get(
          ServerRoutes.connector.fetchConnectionMappings(context.connection_id),
        );
        const normalized = normalizeMappings(data);
        setMappings(normalized);
      } catch {
        // Error handled silently
      }
    };

    fetchMappings();
  }, [context.connection_id]);

  // Save mappings
  const saveMappings = async (
    updated: TableMapping[],
    onSuccess?: () => void,
  ) => {
    try {
      await AxiosInstance.post(
        ServerRoutes.connector.saveConnectionMappings(),
        {
          connection_id: context.connection_id,
          mappings: updated.map((m) => ({
            source: m.sourceTable,
            destination: m.destinationTable,
          })),
        },
      );

      setMappings(updated);
      onMappingsChange?.(updated);
      onSuccess?.();
    } catch (err) {
      const message =
        err && typeof err === "object" && "response" in err
          ? String(
              (
                err as {
                  response?: { data?: { detail?: string; message?: string } };
                }
              ).response?.data?.detail ??
                (err as { response?: { data?: { message?: string } } }).response
                  ?.data?.message ??
                "Failed to save mapping.",
            )
          : "Failed to save mapping.";
      toaster.error({
        title: "Could not save mapping",
        description: message,
      });
    }
  };

  // Handle drop action
  const handleDrop = (sourceTable: string, destinationTable: string) => {
    if (isDisabled) {
      toaster.warning({
        title: "Operation in progress",
        description: "Please wait for the current operation to complete.",
      });
      return;
    }

    if (!sourceTable || sourceTable.trim() === "") {
      toaster.error({
        title: "Error",
        description: "Please drag a source table first.",
      });
      return;
    }

    const existingMapping = mappings.find(
      (m) =>
        m.sourceTable === sourceTable &&
        m.destinationTable === destinationTable,
    );
    if (existingMapping) {
      toaster.warning({
        title: "Mapping Already Exists",
        description: "This mapping already exists.",
      });
      return;
    }

    // Validate table mapping
    if (reverseSchemaData) {
      const sourceTableData = reverseSchemaData.source_tables?.find(
        (t) => t.table === sourceTable,
      );
      const destinationTableData = reverseSchemaData.destination_tables?.find(
        (t) => t.table === destinationTable,
      );

      if (sourceTableData && destinationTableData) {
        const validation = validateTableToTableMapping(
          sourceTableData,
          destinationTableData,
        );

        if (!validation.isValid) {
          toaster.error({
            title: validation.error?.title || "Validation Failed",
            description:
              validation.error?.description ||
              "The mapping is not valid. Please check the table structures.",
          });
          return;
        }
      }
    }

    const newMapping: TableMapping = {
      sourceTable,
      destinationTable,
    };

    const updatedMappings = [...mappings, newMapping];
    saveMappings(updatedMappings, () => {
      toaster.success({
        title: "Mapping Saved",
        description: `The mapping "${sourceTable} → ${destinationTable}" has been saved successfully.`,
      });
    });
  };

  // Check if mapping exists
  const hasMapping = (sourceTable: string, destinationTable: string) => {
    return mappings.some(
      (m) =>
        m.sourceTable === sourceTable &&
        m.destinationTable === destinationTable,
    );
  };

  // Get current mappings
  const getMappings = () => mappings;

  // Handle remove mapping
  const handleRemoveMapping = (mappingToRemove: TableMapping) => {
    if (isDisabled) {
      toaster.warning({
        title: "Operation in progress",
        description: "Please wait for the current operation to complete.",
      });
      return;
    }

    const updatedMappings = mappings.filter(
      (m) =>
        !(
          m.sourceTable === mappingToRemove.sourceTable &&
          m.destinationTable === mappingToRemove.destinationTable
        ),
    );

    saveMappings(updatedMappings, () => {
      toaster.success({
        title: "Mapping Deleted",
        description: `The mapping "${mappingToRemove.sourceTable} → ${mappingToRemove.destinationTable}" has been deleted.`,
      });
    });
  };

  const removeBySourceTable = (sourceTable: string) => {
    const matches = mappings.filter((m) => m.sourceTable === sourceTable);
    if (matches.length === 0) return;
    const updatedMappings = mappings.filter(
      (m) => m.sourceTable !== sourceTable,
    );
    saveMappings(updatedMappings, () => {
      toaster.success({
        title: "Mapping Removed",
        description:
          matches.length === 1
            ? `Removed "${matches[0].sourceTable} → ${matches[0].destinationTable}".`
            : `Removed ${matches.length} mappings for "${sourceTable}".`,
      });
    });
  };

  useImperativeHandle(
    ref,
    () => ({
      handleDrop,
      hasMapping,
      getMappings,
      removeBySourceTable,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rebind when drop inputs change
    [isDisabled, mappings, reverseSchemaData, context.connection_id],
  );

  if (hideUi) {
    return null;
  }

  const safeMappings = mappings || [];

  return (
    <Flex
      direction="column"
      gap={2}
      borderWidth={1}
      borderColor="gray.300"
      borderRadius="lg"
      padding={4}
      bgColor="white"
      w="100%"
      maxW="100%"
      overflow="hidden"
    >
      <Flex direction="column" gap={1} mb={4}>
        <Text fontSize="sm" fontWeight="semibold">
          Mapped Tables
        </Text>
        <Text fontSize="xs" color="gray.500">
          Mapped tables appear in Unassigned until you assign them to a batch.
        </Text>
      </Flex>

      {safeMappings.length === 0 ? (
        <Flex
          direction="column"
          alignItems="center"
          justifyContent="center"
          gap={1}
          p={8}
        >
          <Text fontSize="sm" color="gray.500" textAlign="center">
            No mappings yet. Drag a source table onto a destination.
          </Text>
          <Text fontSize="xs" color="gray.400" textAlign="center">
            After mapping, move tables from Unassigned into a batch.
          </Text>
        </Flex>
      ) : (
        safeMappings.map((mapping, index) => (
          <Flex
            key={`${mapping.sourceTable}-${mapping.destinationTable}-${index}`}
            direction="row"
            gap={2}
            alignItems="center"
            p={2}
            bgColor="gray.50"
            borderRadius="sm"
            justifyContent="space-between"
          >
            <Text fontSize="sm" color="gray.700" flex={1}>
              {mapping.sourceTable} → {mapping.destinationTable}
            </Text>
            <Box
              as="button"
              onClick={() => handleRemoveMapping(mapping)}
              cursor={isDisabled ? "not-allowed" : "pointer"}
              color={isDisabled ? "gray.400" : "red.500"}
              opacity={isDisabled ? 0.5 : 1}
              filter={isDisabled ? "grayscale(100%)" : "none"}
              _hover={{ color: isDisabled ? "gray.400" : "red.700" }}
              p={1}
              display="flex"
              alignItems="center"
              justifyContent="center"
            >
              <CiTrash size={18} />
            </Box>
          </Flex>
        ))
      )}
    </Flex>
  );
});

Mapped.displayName = "Mapped";

export default Mapped;
