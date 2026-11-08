import { useMemo, useState } from "react";

import {
  Box,
  Button,
  Flex,
  IconButton,
  Skeleton,
  Text,
} from "@chakra-ui/react";

import { CiTrash } from "react-icons/ci";
import { GoPlus } from "react-icons/go";

import { useFetchBatches } from "@/queryOptions/connector/schema/useBatches";
import { type UnassignedTable } from "@/types/connectors";

import BatchCard from "./BatchCard";
import BatchPickerModal from "./BatchPickerModal";
import NewBatchModal from "./NewBatchModal";

type DisplayUnassignedRow = UnassignedTable & { pending_only?: boolean };

function mergeUnassigned(
  apiRows: UnassignedTable[],
  pendingRows: UnassignedTable[] | undefined,
  sourceToDestination?: Map<string, string>,
): DisplayUnassignedRow[] {
  const map = new Map<string, DisplayUnassignedRow>();
  const keyOf = (name: string) => name.toLowerCase();

  for (const u of apiRows) {
    const dest =
      u.mapped_destination ||
      sourceToDestination?.get(keyOf(u.table_name)) ||
      undefined;
    map.set(keyOf(u.table_name), {
      ...u,
      mapped_destination: dest,
      pending_only: false,
    });
  }
  for (const p of pendingRows ?? []) {
    const key = keyOf(p.table_name);
    const existing = map.get(key);
    const dest =
      p.mapped_destination ||
      existing?.mapped_destination ||
      sourceToDestination?.get(key) ||
      undefined;
    if (!existing) {
      map.set(key, { ...p, mapped_destination: dest, pending_only: true });
    } else {
      map.set(key, {
        ...existing,
        mapped_destination: dest ?? existing.mapped_destination,
      });
    }
  }
  return Array.from(map.values()).sort((a, b) => {
    const s = (a.sequence ?? 0) - (b.sequence ?? 0);
    if (s !== 0) return s;
    return a.table_name.localeCompare(b.table_name);
  });
}

interface BatchGroupedPanelProps {
  connectionId: number;
  pendingUnassignedTables?: UnassignedTable[];
  transformBadges?: Map<string, string>;
  /**
   * selection = regular ETL / file export (checkbox save).
   * mapping = reverse table-to-table (map first → Unassigned → batches).
   */
  flowHint?: "selection" | "mapping";
  /** Reverse ETL: remove source→destination mapping for this source table. */
  onUnmapSource?: (_sourceTable: string) => void;
  /** Reverse ETL: source table (lower) → destination for Unassigned + batch rows. */
  sourceToDestination?: Map<string, string>;
}

const BatchGroupedPanel = ({
  connectionId,
  pendingUnassignedTables = [],
  transformBadges,
  flowHint = "selection",
  onUnmapSource,
  sourceToDestination,
}: BatchGroupedPanelProps) => {
  const { data, isLoading, isFetching } = useFetchBatches(connectionId);
  const isMappingFlow = flowHint === "mapping";

  const [isNewBatchOpen, setIsNewBatchOpen] = useState(false);
  const [pickerTables, setPickerTables] = useState<string[] | null>(null);

  const batches = useMemo(() => data?.batches ?? [], [data]);
  const apiUnassigned = useMemo<UnassignedTable[]>(
    () => data?.unassigned_tables ?? [],
    [data],
  );

  const displayUnassigned = useMemo(
    () =>
      mergeUnassigned(
        apiUnassigned,
        pendingUnassignedTables,
        sourceToDestination,
      ),
    [apiUnassigned, pendingUnassignedTables, sourceToDestination],
  );

  const showEmptyState =
    !isLoading &&
    !isFetching &&
    batches.length === 0 &&
    displayUnassigned.length === 0 &&
    !isMappingFlow;

  const showUnassignedSection =
    displayUnassigned.length > 0 || (isMappingFlow && !isLoading);

  const unassignedSection = showUnassignedSection ? (
    <Box
      borderWidth={1}
      borderStyle="dashed"
      borderColor="gray.300"
      borderRadius="md"
      bgColor="gray.50"
      display="flex"
      flexDirection="column"
      maxH="44vh"
      minH={0}
      overflow="hidden"
      flexShrink={0}
    >
      <Flex
        alignItems="center"
        gap={2}
        px={3}
        py={2.5}
        minH="40px"
        borderBottomWidth={1}
        borderColor="gray.200"
        borderStyle="dashed"
        flexShrink={0}
      >
        <Text fontSize="sm" fontWeight="semibold">
          Unassigned
        </Text>
        <Text fontSize="xs" color="gray.500">
          ({displayUnassigned.length})
        </Text>
        <Box flex="1" />
        {displayUnassigned.length > 1 && (
          <Button
            size="xs"
            variant="ghost"
            onClick={() =>
              setPickerTables(displayUnassigned.map((u) => u.table_name))
            }
          >
            Move all
          </Button>
        )}
      </Flex>
      {displayUnassigned.length === 0 ? (
        <Flex
          direction="column"
          alignItems="center"
          gap={1}
          px={3}
          py={6}
          color="gray.600"
        >
          <Text fontSize="sm" textAlign="center">
            No tables here yet.
          </Text>
          <Text fontSize="xs" textAlign="center" color="gray.500">
            Drag a source table onto a destination — it lands here, then move it
            into a batch.
          </Text>
        </Flex>
      ) : (
        <Box
          flex="1"
          minH={0}
          overflowY="auto"
          borderWidth={1}
          borderColor="gray.100"
          borderRadius="md"
          mx={2}
          mb={2}
          bg="white"
        >
          {displayUnassigned.map((t, index) => (
            <Flex
              key={t.table_name}
              alignItems="center"
              gap={2}
              minH="36px"
              bgColor={index % 2 === 0 ? "gray.50" : "white"}
              px={2.5}
              py={2}
              borderBottomWidth={index < displayUnassigned.length - 1 ? 1 : 0}
              borderColor="gray.100"
            >
              <Flex direction="column" flex="1" minW={0} gap={0}>
                <Text
                  fontSize="sm"
                  lineHeight="short"
                  truncate
                  title={
                    t.mapped_destination
                      ? `${t.table_name} → ${t.mapped_destination}`
                      : t.table_name
                  }
                >
                  {t.table_name}
                </Text>
                {t.mapped_destination && (
                  <Text fontSize="xs" color="gray.500" truncate>
                    → {t.mapped_destination}
                  </Text>
                )}
              </Flex>
              {t.pending_only && !isMappingFlow && (
                <Text
                  fontSize="xs"
                  color="orange.600"
                  whiteSpace="nowrap"
                  flexShrink={0}
                >
                  Save selection
                </Text>
              )}
              {isMappingFlow && onUnmapSource && (
                <IconButton
                  aria-label={`Remove mapping for ${t.table_name}`}
                  size="xs"
                  variant="ghost"
                  colorPalette="red"
                  flexShrink={0}
                  onClick={() => onUnmapSource(t.table_name)}
                >
                  <CiTrash />
                </IconButton>
              )}
              <IconButton
                aria-label={`Move ${t.table_name} to batch`}
                size="xs"
                variant="outline"
                flexShrink={0}
                onClick={() => setPickerTables([t.table_name])}
              >
                <GoPlus />
              </IconButton>
            </Flex>
          ))}
        </Box>
      )}
    </Box>
  ) : null;

  return (
    <Box minW={0} w="100%">
      <Flex
        direction="column"
        borderWidth={1}
        borderColor="gray.300"
        borderRadius="lg"
        bgColor="white"
        minH="200px"
        maxH="72vh"
        minW={0}
        overflow="hidden"
      >
        <Flex
          justifyContent="space-between"
          alignItems="center"
          gap={2}
          px={4}
          py={3}
          borderBottomWidth={1}
          borderColor="gray.200"
          flexShrink={0}
        >
          <Text fontSize="sm" fontWeight="semibold">
            Migration Batches
            {batches.length > 0 && (
              <Text as="span" ml={2} color="gray.500" fontWeight="normal">
                ({batches.length})
              </Text>
            )}
          </Text>
          <Button
            size="xs"
            colorPalette="brand"
            variant="outline"
            onClick={() => setIsNewBatchOpen(true)}
            flexShrink={0}
          >
            <GoPlus />
            New batch
          </Button>
        </Flex>

        <Flex
          direction="column"
          gap={2}
          p={3}
          overflowY="auto"
          flex="1"
          minH={0}
          alignItems="stretch"
        >
          {isLoading && (
            <>
              <Skeleton height={16} />
              <Skeleton height={16} />
              <Skeleton height={16} />
            </>
          )}

          {showEmptyState && (
            <Flex
              direction="column"
              alignItems="center"
              gap={2}
              color="gray.600"
              py={10}
            >
              <Text fontSize="sm">No tables selected yet.</Text>
              <Text fontSize="xs">
                Pick tables in the schema list to get started.
              </Text>
            </Flex>
          )}

          {!isLoading && unassignedSection}

          {!isLoading &&
            batches.map((b) => (
              <BatchCard
                key={b.id}
                batch={b}
                connectionId={connectionId}
                transformBadges={transformBadges}
                sourceToDestination={sourceToDestination}
              />
            ))}

          {!isLoading &&
            isMappingFlow &&
            batches.length === 0 &&
            displayUnassigned.length > 0 && (
              <Text fontSize="xs" color="gray.500" textAlign="center" px={1}>
                Use + to move unassigned tables into a batch.
              </Text>
            )}
        </Flex>

        <Flex
          direction="column"
          alignItems="center"
          gap={1}
          borderTopWidth={1}
          borderColor="gray.200"
          p={2}
          bgColor="white"
          flexShrink={0}
        >
          <Text fontSize="xs" color="gray.500" textAlign="center" px={2}>
            {isMappingFlow
              ? "Map source → destination → Unassigned → batches. Then open Scheduling for task chains and cron."
              : "Open Scheduling to build task chains and set cron on the root batch."}
          </Text>
        </Flex>
      </Flex>

      <NewBatchModal
        open={isNewBatchOpen}
        onClose={() => setIsNewBatchOpen(false)}
        connectionId={connectionId}
        defaultName={`Batch ${batches.length + 1}`}
      />

      <BatchPickerModal
        open={!!pickerTables && pickerTables.length > 0}
        onClose={() => setPickerTables(null)}
        connectionId={connectionId}
        tables={pickerTables ?? []}
        batches={batches}
      />
    </Box>
  );
};

export default BatchGroupedPanel;
