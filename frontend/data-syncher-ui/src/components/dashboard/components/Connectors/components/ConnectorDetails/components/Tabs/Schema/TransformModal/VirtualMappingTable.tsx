import { useMemo, useRef, useState } from "react";

import { Badge, Box, Button, Flex, IconButton, Text } from "@chakra-ui/react";

import { FiMaximize2, FiMinimize2 } from "react-icons/fi";

import { Tooltip } from "@/components/ui/tooltip";
import { type MappingTableRow } from "@/types/connectors";

import { useVirtualizer } from "@tanstack/react-virtual";

type FilterChip =
  | "all"
  | "unmapped_source"
  | "unmapped_target"
  | "warnings"
  | "removed";

interface VirtualMappingTableProps {
  rows: MappingTableRow[];
  search: string;
  filter: FilterChip;
}

const STATUS_COLOR: Record<string, string> = {
  Direct: "green",
  Constant: "blue",
  Narrowed: "orange",
  "Date check": "yellow",
  "From script": "purple",
  "Not in SAP": "red",
  "Added by user": "teal",
  Overridden: "purple",
  Dropped: "gray",
};

const formatType = (row: MappingTableRow): string => {
  if (row.type_label) return row.type_label;
  const meta = row.target_type || row.source_type;
  if (!meta) return "";
  const datatype = String(meta.datatype || "");
  const length = Number(meta.length || 0);
  if (datatype === "CURR" || datatype === "DATS") return datatype;
  return length ? `${datatype} ${length}` : datatype;
};

const VirtualMappingTable = ({
  rows,
  search,
  filter,
}: VirtualMappingTableProps) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter === "unmapped_source" && row.status !== "Dropped")
        return false;
      if (filter === "unmapped_target" && row.status !== "From script")
        return false;
      if (
        filter === "warnings" &&
        !["Narrowed", "Date check", "Not in SAP"].includes(row.status)
      )
        return false;
      if (!q) return true;
      const sap = (row.sap_column || "").toLowerCase();
      const tgt = (row.target_column || "").toLowerCase();
      const extra = (row.sap_columns || []).join(" ").toLowerCase();
      return sap.includes(q) || tgt.includes(q) || extra.includes(q);
    });
  }, [rows, search, filter]);

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 8,
  });

  const table = (
    <Box
      borderWidth={1}
      borderColor="gray.200"
      borderRadius="md"
      bg="white"
      overflow="hidden"
      position={expanded ? "fixed" : "relative"}
      inset={expanded ? 4 : undefined}
      zIndex={expanded ? 1400 : undefined}
      display="flex"
      flexDirection="column"
    >
      <Flex
        justify="space-between"
        align="center"
        px={3}
        py={2}
        bg="gray.50"
        borderBottomWidth={1}
        flexShrink={0}
      >
        <Text fontSize="sm" fontWeight="semibold" color="gray.700">
          Column mapping ({filtered.length})
        </Text>
        {expanded ? (
          <Button
            size="sm"
            variant="outline"
            color="gray.800"
            borderColor="gray.400"
            bg="white"
            onClick={() => setExpanded(false)}
          >
            <FiMinimize2 /> Collapse
          </Button>
        ) : (
          <Tooltip content="Expand mapping table">
            <IconButton
              aria-label="Expand mapping table"
              size="xs"
              variant="ghost"
              onClick={() => setExpanded(true)}
            >
              <FiMaximize2 />
            </IconButton>
          </Tooltip>
        )}
      </Flex>
      <Box
        ref={parentRef}
        flex="1"
        maxH={expanded ? "calc(100vh - 120px)" : "320px"}
        overflowY="auto"
      >
        <Flex
          px={3}
          py={2}
          bg="gray.50"
          fontSize="xs"
          fontWeight="semibold"
          color="gray.600"
          borderBottomWidth={1}
          position="sticky"
          top={0}
          zIndex={1}
          gap={2}
        >
          <Box flex="1.2">SAP column</Box>
          <Box w="40px" textAlign="center">
            →
          </Box>
          <Box flex="1.2">Mapped column</Box>
          <Box w="90px">Type</Box>
          <Box w="110px">Status</Box>
        </Flex>
        <Box h={`${virtualizer.getTotalSize()}px`} position="relative">
          {virtualizer.getVirtualItems().map((vItem) => {
            const row = filtered[vItem.index];
            const renamed = row.renamed;
            const fromScript = row.status === "From script";
            const dropped = row.status === "Dropped";
            return (
              <Flex
                key={`${row.sap_column}-${row.target_column}-${vItem.index}`}
                position="absolute"
                top={0}
                left={0}
                w="100%"
                h={`${vItem.size}px`}
                transform={`translateY(${vItem.start}px)`}
                px={3}
                py={2}
                align="center"
                borderBottomWidth={1}
                borderColor="gray.100"
                fontSize="sm"
                gap={2}
              >
                <Text
                  flex="1.2"
                  truncate
                  color={row.sap_column ? "gray.800" : "gray.400"}
                >
                  {row.sap_column || "none"}
                </Text>
                <Box w="40px" textAlign="center">
                  {fromScript ? (
                    <Text
                      color="purple.500"
                      fontSize="xs"
                      fontWeight="semibold"
                    >
                      ↗
                    </Text>
                  ) : (
                    <Text
                      color={renamed ? "brand.500" : "gray.400"}
                      fontWeight={renamed ? "bold" : "normal"}
                    >
                      →
                    </Text>
                  )}
                </Box>
                <Text
                  flex="1.2"
                  truncate
                  fontWeight="medium"
                  color={dropped ? "gray.400" : "gray.800"}
                  fontStyle={dropped ? "italic" : undefined}
                >
                  {dropped ? "not mapped" : row.target_column}
                </Text>
                <Text w="90px" fontSize="xs" color="gray.600" truncate>
                  {formatType(row)}
                </Text>
                <Box w="110px">
                  <Badge
                    size="sm"
                    colorPalette={STATUS_COLOR[row.status] || "gray"}
                    variant="subtle"
                  >
                    {row.status}
                  </Badge>
                </Box>
              </Flex>
            );
          })}
        </Box>
        {filtered.length === 0 && (
          <Text p={4} fontSize="sm" color="gray.500">
            No rows match the current filter.
          </Text>
        )}
      </Box>
    </Box>
  );

  return (
    <>
      {expanded && (
        <Box
          position="fixed"
          inset={0}
          bg="blackAlpha.600"
          zIndex={1399}
          onClick={() => setExpanded(false)}
        />
      )}
      {table}
    </>
  );
};

export default VirtualMappingTable;
