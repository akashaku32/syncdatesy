import React, { useMemo, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  Flex,
  Grid,
  HStack,
  Input,
  InputGroup,
  Text,
  VStack,
} from "@chakra-ui/react";

import { IoMdPlay } from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";

interface Column {
  name: string;
  isPrimaryKey?: boolean;
  cardinality?: number;
  warning?: string;
}

interface TableType {
  name: string;
  columns: Column[];
  /** When true, primary keys are already set and cannot be changed. */
  pkLocked?: boolean;
}

interface SchemaData {
  schemaName: string;
  tables: TableType[];
}

interface PrimaryKeySelectionProps {
  schemaData?: SchemaData;
  onBack?: () => void;
  onSaveAndContinue: (_primaryKeys: Record<string, string[]>) => void;
  loading?: boolean;
}

const PrimaryKeySelection: React.FC<PrimaryKeySelectionProps> = ({
  schemaData,
  onBack,
  onSaveAndContinue,
  loading = false,
}) => {
  // Default schema data if none provided
  const defaultSchema: SchemaData = useMemo(() => {
    return (
      schemaData || {
        schemaName: "Schema",
        tables: [
          {
            name: "Table",
            columns: [],
          },
        ],
      }
    );
  }, [schemaData]);

  const [searchQuery, setSearchQuery] = useState("");
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>(
    {},
  );
  const [selectedTable, setSelectedTable] = useState<string | null>(() => {
    return defaultSchema.tables?.[0]?.name || null;
  });
  const [primaryKeys, setPrimaryKeys] = useState<
    Record<string, Record<string, boolean>>
  >({});

  const filteredTables = useMemo(() => {
    if (!searchQuery.trim()) return defaultSchema.tables;
    return defaultSchema.tables.filter((table) =>
      table.name.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [searchQuery, defaultSchema.tables]);

  const togglePrimaryKey = (tableName: string, columnName: string) => {
    // Don't allow toggling if the table is locked
    const table = defaultSchema.tables.find((t) => t.name === tableName);
    if (table?.pkLocked) return;

    setPrimaryKeys((prev) => {
      const currentTablePKs = prev[tableName] || {};

      // If we don't have this column tracked yet, check if it was initially a PK
      const isInitiallyPK =
        defaultSchema.tables
          .find((t) => t.name === tableName)
          ?.columns.find((c) => c.name === columnName)?.isPrimaryKey || false;

      const currentValue =
        currentTablePKs[columnName] !== undefined
          ? currentTablePKs[columnName]
          : isInitiallyPK;

      return {
        ...prev,
        [tableName]: {
          ...currentTablePKs,
          [columnName]: !currentValue,
        },
      };
    });
  };

  React.useEffect(() => {
    if (!defaultSchema.tables.length) return;
    if (
      !selectedTable ||
      !defaultSchema.tables.some((t) => t.name === selectedTable)
    ) {
      setSelectedTable(defaultSchema.tables[0].name);
    }
  }, [defaultSchema.tables, selectedTable]);

  const currentTable = selectedTable
    ? defaultSchema.tables.find((t) => t.name === selectedTable) ||
      defaultSchema.tables[0]
    : defaultSchema.tables[0];

  const handleSaveAndContinue = () => {
    const primaryKeysMap: Record<string, string[]> = {};

    // We need to collect all effective PKs (initial + toggled)
    defaultSchema.tables.forEach((table) => {
      const tablePKs: string[] = [];
      const userToggles = primaryKeys[table.name] || {};

      table.columns.forEach((column) => {
        const isInitiallyPK = column.isPrimaryKey || false;
        const isToggled = userToggles[column.name];

        const isEffectivePK =
          isToggled !== undefined ? isToggled : isInitiallyPK;

        if (isEffectivePK) {
          tablePKs.push(column.name);
        }
      });

      if (tablePKs.length > 0) {
        primaryKeysMap[table.name] = tablePKs;
      }
    });

    onSaveAndContinue(primaryKeysMap);
  };

  return (
    <VStack
      align="stretch"
      gap={6}
      w="100%"
      h="100%"
      bg="gray.50"
      p={8}
      borderRadius="xl"
    >
      {/* Header Section */}
      <VStack align="center" gap={1} mb={2}>
        <Text fontSize="2xl" fontWeight="bold" color="gray.800">
          Select Primary Keys
        </Text>
        <Text fontSize="sm" color="gray.500" textAlign="center">
          Review the connection schema below and select the columns to use as
          the primary key for the table.
        </Text>
      </VStack>

      <Flex mr="auto" w="320px">
        <InputGroup endElement={<MdSearch size={24} />}>
          <Input
            placeholder="Search table name"
            size="md"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </InputGroup>
      </Flex>
      {/* Content Grid */}
      {/* Content Grid */}
      <Grid templateColumns="1fr 1fr" gap={4} minH="500px">
        {/* Left Column: Table List */}
        <Flex
          direction="column"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          padding={4}
          bgColor="white"
          gap={2}
        >
          <Flex mb={4} justifyContent="space-between" alignItems="center">
            <Text fontSize="sm" fontWeight="semibold">
              Table Names
            </Text>
            {/* Placeholder for header actions if needed */}
            <Flex gap={4} alignItems="center"></Flex>
          </Flex>

          {!filteredTables?.length && (
            <Flex direction="column" alignItems="center" py={4}>
              <Text color="gray.500">No Tables available</Text>
            </Flex>
          )}

          <Box flex={1} overflowY="auto">
            {filteredTables.map((table, index) => {
              const isExpanded = !!expandedTables[table.name];
              const isEven = index % 2 === 0;
              const rowBg = isEven ? "gray.100" : "white";
              return (
                <Flex
                  key={table.name}
                  direction="column"
                  bg={rowBg}
                  p={2}
                  borderRadius={4}
                  mb={1}
                >
                  <Grid
                    templateColumns="24px 1fr auto"
                    alignItems="center"
                    width="100%"
                    gap={2}
                  >
                    <Box
                      cursor="pointer"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedTables((prev) => ({
                          ...prev,
                          [table.name]: !prev[table.name],
                        }));
                      }}
                      color="gray.600"
                    >
                      {isExpanded ? <IoCaretDownSharp /> : <IoMdPlay />}
                    </Box>

                    <Box
                      cursor="pointer"
                      onClick={() => setSelectedTable(table.name)}
                    >
                      <Text
                        fontSize="sm"
                        fontWeight="medium"
                        color={table.pkLocked ? "gray.400" : "gray.700"}
                      >
                        {table.name}
                      </Text>
                    </Box>

                    <Text fontSize="xs" color="gray.400">
                      {table.columns.length} cols
                    </Text>
                  </Grid>

                  {isExpanded && (
                    <Flex direction="column" gap={2} mt={2} pl={8}>
                      {table.columns.map((col) => {
                        const userToggled = primaryKeys[table.name]?.[col.name];
                        const isPK =
                          userToggled !== undefined
                            ? userToggled
                            : col.isPrimaryKey;

                        return (
                          <Flex key={col.name} gap={2} alignItems="center">
                            {isPK && (
                              <Text fontSize="xs" color="yellow.600">
                                🔑
                              </Text>
                            )}
                            <Text fontSize="sm" color="gray.600">
                              {col.name}
                            </Text>
                          </Flex>
                        );
                      })}
                    </Flex>
                  )}
                </Flex>
              );
            })}
          </Box>
        </Flex>

        {/* Right Column: Edit Primary Keys */}
        <Flex
          direction="column"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          padding={4}
          bgColor="white"
          gap={2}
        >
          <Flex mb={4} justifyContent="space-between" alignItems="center">
            <Text
              fontSize="xs"
              fontWeight="bold"
              textTransform="uppercase"
              color={currentTable?.pkLocked ? "gray.400" : "gray.500"}
            >
              Edit Primary Keys
            </Text>
            <Flex align="center" gap={2}>
              <Text fontSize="xs" color="gray.500">
                {currentTable?.name || "Select a table"}
              </Text>
            </Flex>
          </Flex>

          <Box flex={1} overflowY="auto">
            {currentTable ? (
              <VStack align="stretch" gap={0}>
                {currentTable.columns.map((column, idx) => {
                  const userToggled =
                    primaryKeys[currentTable.name]?.[column.name];
                  const isChecked =
                    userToggled !== undefined
                      ? userToggled
                      : column.isPrimaryKey || false;

                  return (
                    <Box
                      key={column.name}
                      borderBottomWidth={
                        idx === currentTable.columns.length - 1 ? 0 : 1
                      }
                      borderColor="gray.100"
                    >
                      <Flex align="center" p={2} gap={4}>
                        <Box
                          flex={1}
                          overflow="hidden"
                          display="flex"
                          alignItems="center"
                          gap={2}
                        >
                          {isChecked && (
                            <Text fontSize="sm" color="yellow.600">
                              🔑
                            </Text>
                          )}
                          <Text
                            fontSize="sm"
                            fontWeight="medium"
                            color="gray.700"
                          >
                            {column.name}
                          </Text>
                        </Box>

                        <VStack align="flex-start" gap={2} flex={2}>
                          <HStack gap={2}>
                            <Checkbox.Root
                              colorPalette="brand"
                              size="sm"
                              checked={isChecked}
                              disabled={!!currentTable.pkLocked}
                              onCheckedChange={() =>
                                !currentTable.pkLocked &&
                                togglePrimaryKey(currentTable.name, column.name)
                              }
                            >
                              <Checkbox.HiddenInput />
                              <Checkbox.Control
                                cursor={
                                  currentTable.pkLocked
                                    ? "not-allowed"
                                    : "pointer"
                                }
                                opacity={currentTable.pkLocked ? 0.5 : 1}
                              />
                              <Checkbox.Label
                                fontSize="xs"
                                fontWeight="medium"
                                color={
                                  currentTable.pkLocked
                                    ? "gray.400"
                                    : "gray.600"
                                }
                                cursor={
                                  currentTable.pkLocked
                                    ? "not-allowed"
                                    : "pointer"
                                }
                              >
                                Primary key
                              </Checkbox.Label>
                            </Checkbox.Root>
                          </HStack>

                          {/* Warning removed as requested */}
                        </VStack>
                      </Flex>
                    </Box>
                  );
                })}
              </VStack>
            ) : (
              <Flex align="center" justify="center" h="100%" color="gray.500">
                <Text>Select a table to edit primary keys</Text>
              </Flex>
            )}
          </Box>
        </Flex>
      </Grid>

      {/* Footer Buttons */}
      <Flex justify="flex-end" gap={4} mt={4}>
        {onBack && (
          <Button
            variant="outline"
            bg="white"
            borderColor="gray.300"
            onClick={onBack}
            px={8}
          >
            Back
          </Button>
        )}
        <Button
          colorPalette="brand"
          variant="solid"
          onClick={handleSaveAndContinue}
          loading={loading}
          px={8}
        >
          Save & Continue
        </Button>
      </Flex>
    </VStack>
  );
};

export default PrimaryKeySelection;
