import {
  Box,
  Checkbox,
  Field,
  Flex,
  Grid,
  HStack,
  Input,
  Text,
  VStack,
} from "@chakra-ui/react";

import ColorPicker from "@/components/shared/ColorPicker";
import {
  type ExcelConditionalFormat,
  type ExcelDifferentialStyle,
} from "@/types/connectors";

import { getCssColor } from "../helpers";

interface RuleStyleEditorProps {
  rule: ExcelConditionalFormat;
  ruleStyle: ExcelDifferentialStyle;
  onUpdateRule: (_patch: Partial<ExcelConditionalFormat>) => void;
  onUpdateRuleStyle: (_patch: Partial<ExcelDifferentialStyle>) => void;
}

export default function RuleStyleEditor({
  rule,
  ruleStyle,
  onUpdateRule,
  onUpdateRuleStyle,
}: RuleStyleEditorProps) {
  const isMultipleConditions = rule.type === "multipleconditions";

  return (
    <VStack
      align="stretch"
      gap={1.5}
      borderLeft={{ base: "none", md: "1px solid" }}
      borderColor="gray.200"
      pl={{ base: 0, md: 3 }}
    >
      <Flex justify="space-between" align="center">
        <Text fontSize="xs" fontWeight="semibold" color="gray.600">
          Format to Apply
        </Text>
        {/* Live Preview Pill in Header */}
        <Box
          px={3}
          h="22px"
          display="flex"
          alignItems="center"
          justifyContent="center"
          borderRadius="sm"
          border="1px solid"
          borderColor="gray.300"
          bg={getCssColor(ruleStyle.fill, "#FFFFFF")}
          color={getCssColor(ruleStyle.font_color, "#000000")}
          fontWeight={ruleStyle.bold ? "bold" : "normal"}
          fontStyle={ruleStyle.italic ? "italic" : "normal"}
          fontSize="xs"
          minW="75px"
        >
          Preview
        </Box>
      </Flex>

      <Grid templateColumns="1fr 1fr auto" gap={2} alignItems="end">
        <Field.Root gap={0}>
          <Field.Label fontSize="xs" color="gray.600" mb={0.5}>
            Fill Color (HEX)
          </Field.Label>
          <HStack gap={1}>
            <ColorPicker
              value={ruleStyle.fill}
              onChange={(hex) => onUpdateRuleStyle({ fill: hex })}
              w="14px"
              h="14px"
              borderRadius="full"
              allowNoFill={true}
            />
            <Input
              size="xs"
              h="28px"
              bg="white"
              value={ruleStyle.fill ?? ""}
              onChange={(e) => onUpdateRuleStyle({ fill: e.target.value })}
              placeholder="FFC7CE"
            />
          </HStack>
        </Field.Root>

        <Field.Root gap={0}>
          <Field.Label fontSize="xs" color="gray.600" mb={0.5}>
            Text Color (HEX)
          </Field.Label>
          <HStack gap={1}>
            <ColorPicker
              value={ruleStyle.font_color}
              onChange={(hex) => onUpdateRuleStyle({ font_color: hex })}
              w="14px"
              h="14px"
              borderRadius="full"
            />
            <Input
              size="xs"
              h="28px"
              bg="white"
              value={ruleStyle.font_color ?? ""}
              onChange={(e) =>
                onUpdateRuleStyle({ font_color: e.target.value })
              }
              placeholder="9C0006"
            />
          </HStack>
        </Field.Root>

        <HStack gap={2.5} h="28px" align="center" pl={1}>
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={ruleStyle.bold ?? false}
            onCheckedChange={(details) =>
              onUpdateRuleStyle({
                bold: !!details.checked,
              })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Bold</Checkbox.Label>
          </Checkbox.Root>
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={ruleStyle.italic ?? false}
            onCheckedChange={(details) =>
              onUpdateRuleStyle({
                italic: !!details.checked,
              })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Italic</Checkbox.Label>
          </Checkbox.Root>
        </HStack>
      </Grid>

      {/* Highlight Scope Selection */}
      {!isMultipleConditions && (
        <Flex align="center" gap={3} mt={0.5} minH="24px" wrap="wrap">
          <Text fontSize="xs" fontWeight="semibold" color="gray.600">
            Highlight Scope:
          </Text>
          <HStack gap={3} align="center">
            {/* Cell Only */}
            <Flex
              align="center"
              gap={1.5}
              cursor="pointer"
              userSelect="none"
              onClick={() => {
                onUpdateRule({
                  highlight_scope: "cell",
                });
              }}
            >
              <Box
                w="13px"
                h="13px"
                borderRadius="full"
                border="1.5px solid"
                borderColor={
                  (rule.highlight_scope ?? "cell") === "cell"
                    ? "brand.500"
                    : "gray.300"
                }
                bg="white"
                display="flex"
                alignItems="center"
                justifyContent="center"
              >
                {(rule.highlight_scope ?? "cell") === "cell" && (
                  <Box w="5px" h="5px" borderRadius="full" bg="brand.500" />
                )}
              </Box>
              <Text
                fontSize="xs"
                color="gray.700"
                fontWeight="medium"
                whiteSpace="nowrap"
              >
                Cell Only
              </Text>
            </Flex>

            {/* Entire Column */}
            <Flex
              align="center"
              gap={1.5}
              cursor="pointer"
              userSelect="none"
              onClick={() => {
                onUpdateRule({
                  highlight_scope: "entire_column",
                });
              }}
            >
              <Box
                w="13px"
                h="13px"
                borderRadius="full"
                border="1.5px solid"
                borderColor={
                  rule.highlight_scope === "entire_column"
                    ? "brand.500"
                    : "gray.300"
                }
                bg="white"
                display="flex"
                alignItems="center"
                justifyContent="center"
              >
                {rule.highlight_scope === "entire_column" && (
                  <Box w="5px" h="5px" borderRadius="full" bg="brand.500" />
                )}
              </Box>
              <Text
                fontSize="xs"
                color="gray.700"
                fontWeight="medium"
                whiteSpace="nowrap"
              >
                Entire Column
              </Text>
            </Flex>

            {/* Entire Row */}
            <Flex
              align="center"
              gap={1.5}
              cursor="pointer"
              userSelect="none"
              onClick={() => {
                onUpdateRule({
                  highlight_scope: "entire_row",
                });
              }}
            >
              <Box
                w="13px"
                h="13px"
                borderRadius="full"
                border="1.5px solid"
                borderColor={
                  rule.highlight_scope === "entire_row"
                    ? "brand.500"
                    : "gray.300"
                }
                bg="white"
                display="flex"
                alignItems="center"
                justifyContent="center"
              >
                {rule.highlight_scope === "entire_row" && (
                  <Box w="5px" h="5px" borderRadius="full" bg="brand.500" />
                )}
              </Box>
              <Text
                fontSize="xs"
                color="gray.700"
                fontWeight="medium"
                whiteSpace="nowrap"
              >
                Entire Row
              </Text>
            </Flex>
          </HStack>
        </Flex>
      )}
    </VStack>
  );
}
