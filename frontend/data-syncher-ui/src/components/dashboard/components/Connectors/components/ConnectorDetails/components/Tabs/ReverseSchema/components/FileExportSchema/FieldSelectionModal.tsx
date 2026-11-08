import React, { useEffect, useMemo, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  CloseButton,
  Dialog,
  Flex,
  IconButton,
  Input,
  InputGroup,
  Portal,
  Table,
  Text,
  VStack,
} from "@chakra-ui/react";

import { IoMdOptions } from "react-icons/io";
import {
  MdDragIndicator,
  MdKeyboardArrowDown,
  MdKeyboardArrowUp,
  MdKeyboardDoubleArrowDown,
  MdKeyboardDoubleArrowUp,
  MdSearch,
} from "react-icons/md";
import { PiKeyFill } from "react-icons/pi";

import { type TableFieldInfo } from "@/types/connectors";

import { isPrimaryKey } from "../../utils/validation";

// We need a local Checkbox component if the one in @/components/ui/checkbox is not standard
// But looking at SnowflakeSftpSchema.tsx, it uses Checkbox.Root, etc.

interface FieldSelectionModalProps {
  open: boolean;
  onClose: () => void;
  tableName: string;
  tableFields: Record<string, string | TableFieldInfo>;
  initialSelectedFields: string[] | null | undefined;
  onSave: (_fields: string[]) => void;
  isSaving: boolean;
}

const FieldSelectionModal = ({
  open,
  onClose,
  tableName,
  tableFields,
  initialSelectedFields,
  onSave,
  isSaving,
}: FieldSelectionModalProps) => {
  const allFields = useMemo(() => Object.keys(tableFields), [tableFields]);
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeSelectedFields, setActiveSelectedFields] = useState<string[]>(
    [],
  );
  const [draggedField, setDraggedField] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      const initial =
        initialSelectedFields && initialSelectedFields.length > 0
          ? initialSelectedFields
          : allFields;
      // We wrap in a short delay to avoid "cascading render" lint errors
      // while ensuring the modal state is reset correctly on open.
      const timer = setTimeout(() => {
        setSelectedFields(initial);
        setActiveSelectedFields([]);
        setSearchQuery("");
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [open, initialSelectedFields, allFields]);

  const filteredFields = allFields
    .filter((f) => f.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => {
      const isPKA = isPrimaryKey(a, tableFields[a]);
      const isPKB = isPrimaryKey(b, tableFields[b]);
      if (isPKA && !isPKB) return -1;
      if (!isPKA && isPKB) return 1;
      return 0;
    });

  const toggleField = (field: string) => {
    setSelectedFields((prev) => {
      const isSelected = prev.includes(field);
      if (isSelected) {
        setActiveSelectedFields((active) => active.filter((f) => f !== field));
        return prev.filter((f) => f !== field);
      } else {
        return [...prev, field];
      }
    });
  };

  const handleDragStart = (field: string) => {
    setDraggedField(field);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (targetField: string) => {
    if (!draggedField || draggedField === targetField) return;

    setSelectedFields((prev) => {
      const newList = [...prev];
      const draggedIndex = newList.indexOf(draggedField);
      const targetIndex = newList.indexOf(targetField);

      if (draggedIndex === -1 || targetIndex === -1) return prev;

      newList.splice(draggedIndex, 1);
      newList.splice(targetIndex, 0, draggedField);
      return newList;
    });
    setDraggedField(null);
  };

  const handleSelectAll = () => {
    setSelectedFields(allFields);
  };

  const handleRowClick = (
    field: string,
    index: number,
    event: React.MouseEvent,
  ) => {
    if (event.ctrlKey || event.metaKey) {
      setActiveSelectedFields((prev) =>
        prev.includes(field)
          ? prev.filter((f) => f !== field)
          : [...prev, field],
      );
    } else if (event.shiftKey && activeSelectedFields.length > 0) {
      const lastSelectedField =
        activeSelectedFields[activeSelectedFields.length - 1];
      const startIndex = selectedFields.indexOf(lastSelectedField);
      const endIndex = index;

      const start = Math.min(startIndex, endIndex);
      const end = Math.max(startIndex, endIndex);
      const newSelections = selectedFields.slice(start, end + 1);

      setActiveSelectedFields(
        Array.from(new Set([...activeSelectedFields, ...newSelections])),
      );
    } else {
      setActiveSelectedFields([field]);
    }
  };

  const selectedIndices = activeSelectedFields
    .map((f) => selectedFields.indexOf(f))
    .filter((i) => i !== -1)
    .sort((a, b) => a - b);

  const moveField = (direction: "top" | "up" | "down" | "bottom") => {
    if (activeSelectedFields.length === 0) return;

    const newFields = [...selectedFields];
    const currentIndices = activeSelectedFields
      .map((f) => newFields.indexOf(f))
      .filter((i) => i !== -1)
      .sort((a, b) => a - b);

    if (currentIndices.length === 0) return;

    if (direction === "up") {
      if (currentIndices[0] === 0) return;
      for (const index of currentIndices) {
        [newFields[index], newFields[index - 1]] = [
          newFields[index - 1],
          newFields[index],
        ];
      }
    } else if (direction === "down") {
      if (currentIndices[currentIndices.length - 1] === newFields.length - 1)
        return;
      for (let i = currentIndices.length - 1; i >= 0; i--) {
        const index = currentIndices[i];
        [newFields[index], newFields[index + 1]] = [
          newFields[index + 1],
          newFields[index],
        ];
      }
    } else if (direction === "top") {
      if (currentIndices[0] === 0) return;
      const itemsToMove = currentIndices.map((i) => newFields[i]);
      const remainingItems = newFields.filter(
        (_, i) => !currentIndices.includes(i),
      );
      newFields.length = 0;
      newFields.push(...itemsToMove, ...remainingItems);
    } else if (direction === "bottom") {
      if (currentIndices[currentIndices.length - 1] === newFields.length - 1)
        return;
      const itemsToMove = currentIndices.map((i) => newFields[i]);
      const remainingItems = newFields.filter(
        (_, i) => !currentIndices.includes(i),
      );
      newFields.length = 0;
      newFields.push(...remainingItems, ...itemsToMove);
    }

    setSelectedFields(newFields);
  };

  const handleSave = () => {
    onSave(selectedFields);
  };

  return (
    <Dialog.Root lazyMount open={open} size="xl">
      <Portal>
        <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
        <Dialog.Positioner>
          <Dialog.Content
            borderRadius="xl"
            boxShadow="2xl"
            bg="white"
            overflow="hidden"
          >
            <Dialog.Header
              bg="brand.50"
              borderBottomWidth="1px"
              borderColor="brand.100"
            >
              <Dialog.Title color="brand.800" fontWeight="bold">
                Select and Order Fields: {tableName}
              </Dialog.Title>
            </Dialog.Header>
            <Dialog.Body p={6}>
              <VStack align="stretch" gap={6}>
                <InputGroup
                  width="100%"
                  endElement={<MdSearch size={22} color="gray" />}
                >
                  <Input
                    placeholder="Search fields by name..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    variant="outline"
                    colorPalette="brand"
                    borderRadius="full"
                    bg="gray.50"
                  />
                </InputGroup>

                <Flex gap={6} height="450px">
                  {/* Left: Available Fields */}
                  <Box
                    flex={1}
                    borderWidth={1}
                    borderRadius="lg"
                    borderColor="gray.200"
                    bg="gray.50"
                    overflow="hidden"
                    display="flex"
                    flexDirection="column"
                  >
                    <Box
                      p={3}
                      bg="white"
                      borderBottomWidth={1}
                      borderColor="gray.100"
                    >
                      <Flex justifyContent="space-between" alignItems="center">
                        <Text
                          fontWeight="bold"
                          fontSize="xs"
                          color="gray.600"
                          letterSpacing="wider"
                        >
                          Available Fields ({filteredFields.length})
                        </Text>
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={handleSelectAll}
                          colorPalette="brand"
                        >
                          Select all
                        </Button>
                      </Flex>
                    </Box>
                    <Box flex={1} overflowY="auto" p={2}>
                      <Table.Root size="sm" variant="line" stickyHeader>
                        <Table.Header>
                          <Table.Row bg="gray.50">
                            <Table.ColumnHeader
                              width="40px"
                              py={2}
                            ></Table.ColumnHeader>
                            <Table.ColumnHeader py={2}>
                              Field
                            </Table.ColumnHeader>
                            <Table.ColumnHeader py={2}>Type</Table.ColumnHeader>
                          </Table.Row>
                        </Table.Header>
                        <Table.Body>
                          {filteredFields.map((field) => {
                            const fieldInfo = tableFields[field];
                            const dataType =
                              typeof fieldInfo === "string"
                                ? fieldInfo
                                : (fieldInfo as { data_type: string })
                                    .data_type;
                            const isSelected = selectedFields.includes(field);

                            return (
                              <Table.Row
                                key={field}
                                _hover={{ bg: "brand.50" }}
                                transition="background 0.2s"
                                bg={isSelected ? "brand.50" : "transparent"}
                              >
                                <Table.Cell py={2}>
                                  <Checkbox.Root
                                    colorPalette="brand"
                                    checked={isSelected}
                                    onCheckedChange={() => toggleField(field)}
                                  >
                                    <Checkbox.HiddenInput />
                                    <Checkbox.Control />
                                  </Checkbox.Root>
                                </Table.Cell>
                                <Table.Cell
                                  py={2}
                                  fontSize="xs"
                                  fontWeight={
                                    isSelected ? "semibold" : "normal"
                                  }
                                >
                                  <Flex alignItems="center" gap={1}>
                                    {isPrimaryKey(
                                      field,
                                      fieldInfo as
                                        | string
                                        | { data_type: string },
                                    ) && (
                                      <Box color="yellow.500">
                                        <PiKeyFill />
                                      </Box>
                                    )}
                                    {field}
                                  </Flex>
                                </Table.Cell>
                                <Table.Cell
                                  py={2}
                                  fontSize="xs"
                                  color="gray.500"
                                >
                                  {dataType}
                                </Table.Cell>
                              </Table.Row>
                            );
                          })}
                        </Table.Body>
                      </Table.Root>
                    </Box>
                  </Box>

                  {/* Right: Selected & Order */}
                  <Box
                    flex={1}
                    borderWidth={1}
                    borderRadius="lg"
                    borderColor="brand.100"
                    bg="white"
                    overflow="hidden"
                    display="flex"
                    flexDirection="column"
                    boxShadow="sm"
                  >
                    <Box
                      p={3}
                      bg="brand.50"
                      borderBottomWidth={1}
                      borderColor="brand.100"
                    >
                      <Flex
                        justifyContent="space-between"
                        alignItems="center"
                        gap={2}
                      >
                        <Text
                          fontWeight="bold"
                          fontSize="xs"
                          color="brand.800"
                          letterSpacing="wider"
                          whiteSpace="nowrap"
                        >
                          Selected Fields ({selectedFields.length})
                        </Text>

                        <Flex gap={1} alignItems="center">
                          <IconButton
                            aria-label="Move to Top"
                            size="xs"
                            variant="subtle"
                            disabled={
                              selectedIndices.length === 0 ||
                              selectedIndices.includes(0)
                            }
                            onClick={() => moveField("top")}
                          >
                            <MdKeyboardDoubleArrowUp />
                          </IconButton>
                          <IconButton
                            aria-label="Move Up"
                            size="xs"
                            variant="subtle"
                            disabled={
                              selectedIndices.length === 0 ||
                              selectedIndices.includes(0)
                            }
                            onClick={() => moveField("up")}
                          >
                            <MdKeyboardArrowUp />
                          </IconButton>
                          <IconButton
                            aria-label="Move Down"
                            size="xs"
                            variant="subtle"
                            disabled={
                              selectedIndices.length === 0 ||
                              selectedIndices.includes(
                                selectedFields.length - 1,
                              )
                            }
                            onClick={() => moveField("down")}
                          >
                            <MdKeyboardArrowDown />
                          </IconButton>
                          <IconButton
                            aria-label="Move to Bottom"
                            size="xs"
                            variant="subtle"
                            disabled={
                              selectedIndices.length === 0 ||
                              selectedIndices.includes(
                                selectedFields.length - 1,
                              )
                            }
                            onClick={() => moveField("bottom")}
                          >
                            <MdKeyboardDoubleArrowDown />
                          </IconButton>
                        </Flex>
                      </Flex>
                    </Box>
                    <Box flex={1} overflowY="auto" p={3}>
                      {selectedFields.length === 0 ? (
                        <Flex
                          height="100%"
                          direction="column"
                          alignItems="center"
                          justifyContent="center"
                          gap={2}
                        >
                          <IoMdOptions size={40} color="#CBD5E0" />
                          <Text
                            color="gray.400"
                            fontSize="xs"
                            textAlign="center"
                            px={4}
                          >
                            No fields selected.
                            <br />
                            All fields will be included in the export.
                          </Text>
                        </Flex>
                      ) : (
                        <VStack align="stretch" gap={2}>
                          {selectedFields.map((field, index) => {
                            const isActive =
                              activeSelectedFields.includes(field);
                            return (
                              <Flex
                                key={field}
                                p={2.5}
                                bg={isActive ? "brand.50" : "white"}
                                borderWidth={1}
                                borderColor={
                                  isActive ? "brand.200" : "gray.100"
                                }
                                borderRadius="md"
                                alignItems="center"
                                gap={3}
                                transition="all 0.2s"
                                cursor="pointer"
                                onClick={(e) => handleRowClick(field, index, e)}
                                draggable
                                onDragStart={() => handleDragStart(field)}
                                onDragOver={handleDragOver}
                                onDrop={() => handleDrop(field)}
                                onDragEnd={() => setDraggedField(null)}
                              >
                                <Box color="#A0AEC0">
                                  <MdDragIndicator />
                                </Box>
                                <Text
                                  flex={1}
                                  fontSize="xs"
                                  fontWeight="medium"
                                  color={isActive ? "brand.800" : "gray.700"}
                                  truncate
                                >
                                  {field}
                                </Text>
                              </Flex>
                            );
                          })}
                        </VStack>
                      )}
                    </Box>
                    <Box
                      p={2}
                      borderTopWidth={1}
                      borderColor="gray.100"
                      bg="gray.50"
                    >
                      <Text fontSize="10px" color="gray.500" textAlign="center">
                        Click to select · Ctrl+click or Shift+click for
                        multi-select
                      </Text>
                    </Box>
                  </Box>
                </Flex>

                <Text fontSize="sm" color="gray.600" fontWeight="medium">
                  {selectedFields.length} fields selected for export
                </Text>
              </VStack>
            </Dialog.Body>
            <Dialog.Footer
              bg="gray.50"
              borderTopWidth="1px"
              borderColor="gray.100"
              p={4}
            >
              <Button variant="ghost" onClick={onClose} mr={3}>
                Cancel
              </Button>
              <Button
                colorPalette="brand"
                loading={isSaving}
                onClick={handleSave}
                px={8}
                borderRadius="full"
              >
                Save Field Selection
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton
                size="sm"
                onClick={onClose}
                position="absolute"
                right={4}
                top={4}
              />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default FieldSelectionModal;
