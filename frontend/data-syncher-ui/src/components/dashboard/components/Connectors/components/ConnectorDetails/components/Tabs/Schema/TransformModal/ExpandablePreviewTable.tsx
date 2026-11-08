import { useMemo, useRef, useState } from "react";

import { Box, Button, Flex, IconButton, Input, Text } from "@chakra-ui/react";

import { FiMaximize2, FiMinimize2 } from "react-icons/fi";

import { Tooltip } from "@/components/ui/tooltip";

import { useVirtualizer } from "@tanstack/react-virtual";

interface ExpandablePreviewTableProps {
  title: string;
  rows: Record<string, unknown>[];
}

const ExpandablePreviewTable = ({
  title,
  rows,
}: ExpandablePreviewTableProps) => {
  const parentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState("");

  const columns = useMemo(() => {
    if (!rows.length) return [];
    return Object.keys(rows[0]);
  }, [rows]);

  const visibleColumns = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return columns;
    return columns.filter((c) => c.toLowerCase().includes(q));
  }, [columns, search]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 36,
    overscan: 6,
  });

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
          gap={2}
        >
          <Text fontSize="sm" fontWeight="semibold">
            {title} ({rows.length} rows · {visibleColumns.length} columns)
          </Text>
          <Flex gap={2} align="center">
            <Input
              size="xs"
              w="160px"
              placeholder="Search columns"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
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
              <Tooltip content="Expand preview">
                <IconButton
                  aria-label="Expand preview"
                  size="xs"
                  variant="ghost"
                  onClick={() => setExpanded(true)}
                >
                  <FiMaximize2 />
                </IconButton>
              </Tooltip>
            )}
          </Flex>
        </Flex>
        <Box
          ref={parentRef}
          overflow="auto"
          maxH={expanded ? "calc(100vh - 120px)" : "220px"}
          flex="1"
        >
          <Box minW={`${Math.max(visibleColumns.length, 1) * 120}px`}>
            <Flex
              px={2}
              py={2}
              bg="gray.50"
              fontSize="xs"
              fontWeight="semibold"
              position="sticky"
              top={0}
              zIndex={1}
              borderBottomWidth={1}
            >
              {visibleColumns.map((col) => (
                <Box key={col} minW="120px" px={1} truncate title={col}>
                  {col}
                </Box>
              ))}
            </Flex>
            <Box h={`${virtualizer.getTotalSize()}px`} position="relative">
              {virtualizer.getVirtualItems().map((vItem) => {
                const row = rows[vItem.index];
                return (
                  <Flex
                    key={vItem.index}
                    position="absolute"
                    top={0}
                    left={0}
                    w="100%"
                    h={`${vItem.size}px`}
                    transform={`translateY(${vItem.start}px)`}
                    px={2}
                    py={1}
                    fontSize="xs"
                    borderBottomWidth={1}
                    borderColor="gray.100"
                    align="center"
                  >
                    {visibleColumns.map((col) => (
                      <Box key={col} minW="120px" px={1} truncate>
                        {row[col] === null || row[col] === undefined
                          ? ""
                          : String(row[col])}
                      </Box>
                    ))}
                  </Flex>
                );
              })}
            </Box>
          </Box>
        </Box>
      </Box>
    </>
  );
};

export default ExpandablePreviewTable;
