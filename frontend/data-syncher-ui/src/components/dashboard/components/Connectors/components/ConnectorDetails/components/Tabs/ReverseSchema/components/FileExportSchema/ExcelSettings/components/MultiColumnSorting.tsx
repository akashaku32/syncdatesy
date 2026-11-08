import {
  Box,
  Button,
  Flex,
  HStack,
  IconButton,
  NativeSelect,
  Text,
  VStack,
} from "@chakra-ui/react";

import { IoMdAdd, IoMdTrash } from "react-icons/io";

import { type ExcelOptions, type ExcelSortColumn } from "@/types/connectors";

interface MultiColumnSortingProps {
  options: ExcelOptions;
  colNames: string[];
  updateOptions: (_patch: Partial<ExcelOptions>) => void;
}

export const MultiColumnSorting = ({
  options,
  colNames,
  updateOptions,
}: MultiColumnSortingProps) => {
  const sortColumns = options.sort_columns || [];

  const handleAddSort = () => {
    const defaultCol = colNames[0] || "";
    updateOptions({
      sort_columns: [
        ...sortColumns,
        { column_name: defaultCol, direction: "asc" },
      ],
    });
  };

  const handleUpdateSort = (index: number, patch: Partial<ExcelSortColumn>) => {
    const updated = [...sortColumns];
    updated[index] = {
      ...updated[index],
      ...patch,
    };
    updateOptions({ sort_columns: updated });
  };

  const handleRemoveSort = (index: number) => {
    const updated = sortColumns.filter((_, i) => i !== index);
    updateOptions({ sort_columns: updated });
  };

  return (
    <Box
      border="1px solid"
      borderColor="gray.200"
      borderRadius="md"
      p={2.5}
      bg="gray.50"
      mt={1}
    >
      <Flex justifyContent="space-between" alignItems="center" mb={1.5}>
        <Text fontSize="xs" fontWeight="bold" color="gray.700">
          Multi-Column Sorting
        </Text>
        <Button
          size="xs"
          h="24px"
          px={2}
          fontSize="11px"
          variant="surface"
          colorPalette="brand"
          onClick={handleAddSort}
        >
          <IoMdAdd /> Add Sort Column
        </Button>
      </Flex>

      {sortColumns.length === 0 ? (
        <Text fontSize="xs" color="gray.500" fontStyle="italic">
          No sorting rules specified. Order will default to query stream
          sequence.
        </Text>
      ) : (
        <VStack gap={1.5} align="stretch">
          {sortColumns.map((sortItem: ExcelSortColumn, sIdx: number) => (
            <HStack key={sIdx} gap={2} alignItems="center">
              <NativeSelect.Root
                size="xs"
                width={{ base: "100%", md: "260px" }}
                flex={{ base: 1, md: "none" }}
              >
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={sortItem.column_name}
                  onChange={(e) =>
                    handleUpdateSort(sIdx, { column_name: e.target.value })
                  }
                >
                  <option value="">-- Select Column --</option>
                  {colNames.map((col) => (
                    <option key={col} value={col}>
                      {col}
                    </option>
                  ))}
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>

              <NativeSelect.Root
                size="xs"
                width={{ base: "100%", md: "160px" }}
                flex={{ base: 1, md: "none" }}
              >
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={sortItem.direction}
                  onChange={(e) =>
                    handleUpdateSort(sIdx, {
                      direction: e.target.value as "asc" | "desc",
                    })
                  }
                >
                  <option value="asc">Ascending (ASC)</option>
                  <option value="desc">Descending (DESC)</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>

              <IconButton
                aria-label="Remove sort rule"
                size="xs"
                variant="ghost"
                colorPalette="red"
                onClick={() => handleRemoveSort(sIdx)}
              >
                <IoMdTrash />
              </IconButton>
            </HStack>
          ))}
        </VStack>
      )}
    </Box>
  );
};
