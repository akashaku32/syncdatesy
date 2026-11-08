import { Box, Button, Flex, Text, VStack } from "@chakra-ui/react";

import { IoMdAdd } from "react-icons/io";

import { type ConditionalFormattingTabProps } from "../helpers";
import RuleCard from "./formatting/RuleCard";

export default function ConditionalFormattingTab({
  rules,
  colNames,
  tableFields,
  onAddRule,
  onUpdateRule,
  onUpdateRuleStyle,
  onDeleteRule,
}: ConditionalFormattingTabProps) {
  return (
    <VStack gap={3} align="stretch">
      <Flex justifyContent="space-between" alignItems="center">
        <Text fontSize="xs" fontWeight="semibold" color="gray.700">
          Formatting Rules ({rules.length})
        </Text>
        <Button
          size="xs"
          variant="surface"
          colorPalette="brand"
          onClick={onAddRule}
        >
          <IoMdAdd /> Add Rule
        </Button>
      </Flex>

      {rules.length === 0 ? (
        <Box
          py={8}
          textAlign="center"
          border="1px dashed"
          borderColor="gray.300"
          borderRadius="md"
          bg="white"
        >
          <Text fontSize="xs" color="gray.500">
            No conditional formatting rules added yet.
          </Text>
        </Box>
      ) : (
        <VStack gap={2} align="stretch">
          {rules.map((rule, idx) => (
            <RuleCard
              key={idx}
              rule={rule}
              index={idx}
              totalRules={rules.length}
              colNames={colNames}
              tableFields={tableFields}
              onUpdateRule={onUpdateRule}
              onUpdateRuleStyle={onUpdateRuleStyle}
              onDeleteRule={onDeleteRule}
            />
          ))}
        </VStack>
      )}
    </VStack>
  );
}
