import { useEffect, useMemo, useRef, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  Flex,
  HStack,
  Input,
  Text,
  VStack,
} from "@chakra-ui/react";

import {
  MdKeyboardArrowDown,
  MdKeyboardArrowUp,
  MdSearch,
} from "react-icons/md";

interface MultiColumnSelectProps {
  allColumns: string[];
  selectedColumns: string[];
  onChange: (_selected: string[]) => void;
  placeholder?: string;
}

export const MultiColumnSelect = ({
  allColumns,
  selectedColumns,
  onChange,
  placeholder = "-- Select Target Column(s) --",
}: MultiColumnSelectProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const toggleColumn = (col: string) => {
    if (selectedColumns.includes(col)) {
      onChange(selectedColumns.filter((c) => c !== col));
    } else {
      onChange([...selectedColumns, col]);
    }
  };

  const handleSelectAll = () => {
    if (selectedColumns.length === allColumns.length) {
      onChange([]);
    } else {
      onChange([...allColumns]);
    }
  };

  const filteredColumns = useMemo(() => {
    if (!search.trim()) return allColumns;
    const lower = search.toLowerCase();
    return allColumns.filter((c) => c.toLowerCase().includes(lower));
  }, [allColumns, search]);

  return (
    <Box position="relative" ref={popoverRef} width="100%">
      <Button
        size="xs"
        variant="outline"
        bg="white"
        borderColor="gray.300"
        width="100%"
        justifyContent="space-between"
        fontWeight="normal"
        px={2}
        h="28px"
        onClick={() => setIsOpen(!isOpen)}
      >
        <Flex
          gap={1}
          align="center"
          overflow="hidden"
          textOverflow="ellipsis"
          whiteSpace="nowrap"
          flex={1}
        >
          {selectedColumns.length === 0 ? (
            <Text as="span" color="gray.400" fontSize="xs">
              {placeholder}
            </Text>
          ) : selectedColumns.length <= 2 ? (
            selectedColumns.map((col) => (
              <Box
                key={col}
                bg="brand.50"
                color="brand.700"
                px={1.5}
                py={0.5}
                borderRadius="xs"
                fontSize="10px"
                fontWeight="semibold"
                border="1px solid"
                borderColor="brand.200"
              >
                {col}
              </Box>
            ))
          ) : (
            <Text as="span" color="gray.800" fontSize="xs" fontWeight="medium">
              {selectedColumns.length} columns selected
            </Text>
          )}
        </Flex>
        <Box color="gray.500" fontSize="14px" ml={1}>
          {isOpen ? <MdKeyboardArrowUp /> : <MdKeyboardArrowDown />}
        </Box>
      </Button>

      {isOpen && (
        <Box
          position="absolute"
          bottom="calc(100% + 4px)"
          left={0}
          right={0}
          zIndex={1000}
          bg="white"
          border="1px solid"
          borderColor="gray.200"
          borderRadius="md"
          shadow="lg"
          p={2}
        >
          <Flex
            justify="space-between"
            align="center"
            pb={1.5}
            mb={1.5}
            borderBottom="1px solid"
            borderColor="gray.100"
          >
            <Text as="span" fontSize="xs" color="gray.700" fontWeight="bold">
              Target Columns ({selectedColumns.length})
            </Text>
            <Button
              size="xs"
              variant="ghost"
              colorPalette="brand"
              onClick={handleSelectAll}
              h="20px"
              px={1.5}
              fontSize="10px"
            >
              {selectedColumns.length === allColumns.length
                ? "Clear All"
                : "Select All"}
            </Button>
          </Flex>

          {allColumns.length > 5 && (
            <HStack
              mb={1.5}
              bg="gray.50"
              px={2}
              py={0.5}
              borderRadius="sm"
              border="1px solid"
              borderColor="gray.200"
            >
              <MdSearch color="gray" size={13} />
              <Input
                size="xs"
                variant="subtle"
                bg="transparent"
                border="none"
                h="22px"
                p={0}
                fontSize="11px"
                placeholder="Search columns..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </HStack>
          )}

          <VStack align="stretch" gap="2px" maxH="170px" overflowY="auto">
            {filteredColumns.length === 0 ? (
              <Text fontSize="10px" color="gray.400" textAlign="center" py={2}>
                No matching columns
              </Text>
            ) : (
              filteredColumns.map((col) => {
                const isSelected = selectedColumns.includes(col);
                return (
                  <Flex
                    key={col}
                    align="center"
                    gap={2}
                    px={2}
                    py={1}
                    minH="29px"
                    borderRadius="sm"
                    cursor="pointer"
                    _hover={{ bg: "gray.50" }}
                    onClick={() => toggleColumn(col)}
                  >
                    <Checkbox.Root
                      size="xs"
                      colorPalette="brand"
                      checked={isSelected}
                      pointerEvents="none"
                    >
                      <Checkbox.HiddenInput />
                      <Checkbox.Control />
                    </Checkbox.Root>
                    <Text
                      as="span"
                      m={0}
                      p={0}
                      fontSize="xs"
                      lineHeight="normal"
                      color="gray.700"
                      fontWeight={isSelected ? "semibold" : "normal"}
                    >
                      {col}
                    </Text>
                  </Flex>
                );
              })
            )}
          </VStack>
        </Box>
      )}
    </Box>
  );
};
