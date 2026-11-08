import { Badge, Box, Table, Text } from "@chakra-ui/react";

import { type TableChange } from "@/types/connectors";

interface TableSelectionDetailsProps {
  changes: TableChange[];
}

const TableSelectionDetails = ({ changes }: TableSelectionDetailsProps) => {
  const added = changes.filter((c) => c.action.toLowerCase().includes("added"));
  const removed = changes.filter((c) =>
    c.action.toLowerCase().includes("removed"),
  );
  const reordered = changes
    .filter((c) => c.action.toLowerCase().includes("reordered"))
    .sort((a, b) => (a.new_sequence ?? 0) - (b.new_sequence ?? 0));

  const maxRows = Math.max(added.length, removed.length, reordered.length);
  const rows = Array.from({ length: maxRows });

  if (changes.length === 0) {
    return (
      <Box
        w="100%"
        h="full"
        bg="white"
        p={8}
        textAlign="center"
        borderRadius="md"
        border="1px solid"
        borderColor="gray.200"
      >
        <Text color="gray.500">No table changes recorded.</Text>
      </Box>
    );
  }

  return (
    <Box w="100%" h="full" overflowY="auto">
      <Table.Root>
        <Table.Header bg="gray.50">
          <Table.Row>
            <Table.ColumnHeader
              width="33%"
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
            >
              <Box display="flex" alignItems="center" gap={2}>
                Tables Added
                <Badge
                  colorScheme="green"
                  variant="solid"
                  borderRadius="full"
                  fontSize="xs"
                  px={1.5}
                  h="16px"
                  display="flex"
                  alignItems="center"
                >
                  {added.length}
                </Badge>
              </Box>
            </Table.ColumnHeader>
            <Table.ColumnHeader
              width="33%"
              fontWeight="bold"
              color="gray.600"
              borderRightWidth={1}
              borderColor="gray.200"
              py={1}
            >
              <Box display="flex" alignItems="center" gap={2}>
                Tables Removed
                <Badge
                  colorScheme="red"
                  variant="solid"
                  borderRadius="full"
                  fontSize="xs"
                  px={1.5}
                  h="16px"
                  display="flex"
                  alignItems="center"
                >
                  {removed.length}
                </Badge>
              </Box>
            </Table.ColumnHeader>
            <Table.ColumnHeader
              width="33%"
              fontWeight="bold"
              color="gray.600"
              py={1}
            >
              <Box display="flex" alignItems="center" gap={2}>
                Tables Reordered
                <Badge
                  colorScheme="blue"
                  variant="solid"
                  borderRadius="full"
                  fontSize="xs"
                  px={1.5}
                  h="16px"
                  display="flex"
                  alignItems="center"
                >
                  {reordered.length}
                </Badge>
              </Box>
            </Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {rows.map((_, index) => {
            const addedItem = added[index];
            const removedItem = removed[index];
            const reorderedItem = reordered[index];

            return (
              <Table.Row key={index} bg="white" _hover={{ bg: "gray.50" }}>
                <Table.Cell
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                  verticalAlign="top"
                >
                  {addedItem ? (
                    <Text fontSize="sm" color="gray.700" fontWeight="medium">
                      {addedItem.table}
                    </Text>
                  ) : null}
                </Table.Cell>
                <Table.Cell
                  py={0.5}
                  borderRightWidth={1}
                  borderColor="gray.200"
                  verticalAlign="top"
                >
                  {removedItem ? (
                    <Text fontSize="sm" color="gray.700" fontWeight="medium">
                      {removedItem.table}
                    </Text>
                  ) : null}
                </Table.Cell>
                <Table.Cell py={0.5} verticalAlign="top">
                  {reorderedItem ? (
                    <Box>
                      <Text fontSize="sm" color="gray.700" fontWeight="medium">
                        {reorderedItem.table}
                      </Text>
                      {reorderedItem.old_sequence !== undefined &&
                        reorderedItem.new_sequence !== undefined && (
                          <Text fontSize="xs" color="gray.400">
                            Seq: {reorderedItem.old_sequence} &rarr;{" "}
                            {reorderedItem.new_sequence}
                          </Text>
                        )}
                    </Box>
                  ) : null}
                </Table.Cell>
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};

export default TableSelectionDetails;
