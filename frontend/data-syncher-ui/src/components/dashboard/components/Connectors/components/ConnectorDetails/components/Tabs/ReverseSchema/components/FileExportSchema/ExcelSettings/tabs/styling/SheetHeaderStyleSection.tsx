import {
  Box,
  Checkbox,
  Field,
  Flex,
  HStack,
  Input,
  NativeSelect,
  Text,
  VStack,
} from "@chakra-ui/react";

import ColorPickerInput from "../../components/ColorPickerInput";
import ExcelFontSelector from "../../components/ExcelFontSelector";
import {
  type SheetHeaderStyleSectionProps,
  getCssAlignItems,
  getCssColor,
} from "../../helpers";

interface ExtendedSheetHeaderProps extends SheetHeaderStyleSectionProps {
  tableName?: string;
}

export default function SheetHeaderStyleSection({
  options,
  sheetHeaderStyle,
  isSheetHeaderEnabled,
  tableName,
  updateOptions,
  updateSheetHeaderStyle,
}: ExtendedSheetHeaderProps) {
  return (
    <VStack gap={2.5} align="stretch">
      {/* Row 1: Enable Sheet Header */}
      <Box>
        <Checkbox.Root
          size="xs"
          colorPalette="brand"
          checked={isSheetHeaderEnabled}
          onCheckedChange={(details) => {
            const checked = !!details.checked;
            if (checked) {
              updateOptions({
                sheet_header_enabled: true,
                sheet_header: options.sheet_header || "",
                sheet_header_row_span: options.sheet_header_row_span || 1,
              });
            } else {
              updateOptions({
                sheet_header_enabled: false,
              });
            }
          }}
          h="28px"
        >
          <Checkbox.HiddenInput />
          <Checkbox.Control />
          <Checkbox.Label fontSize="xs" fontWeight="bold" color="gray.700">
            Enable Sheet Header
          </Checkbox.Label>
        </Checkbox.Root>
      </Box>

      {isSheetHeaderEnabled && (
        <>
          {/* Row 1: Title Text, Row Span, Horizontal, and Vertical Alignment */}
          <Flex gap={2.5} align="flex-end" wrap="wrap">
            <Field.Root gap={0} flex={1} minW="220px">
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Sheet Header Title Text
              </Field.Label>
              <Input
                size="xs"
                h="28px"
                bg="white"
                value={options.sheet_header ?? ""}
                onChange={(e) =>
                  updateOptions({ sheet_header: e.target.value })
                }
                placeholder="e.g. Weekly Stock Report"
              />
            </Field.Root>

            <Field.Root
              gap={0}
              width={{ base: "100%", sm: "140px" }}
              flexShrink={0}
            >
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Row Span (Height)
              </Field.Label>
              <NativeSelect.Root size="xs">
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={options.sheet_header_row_span ?? 1}
                  onChange={(e) =>
                    updateOptions({
                      sheet_header_row_span: parseInt(e.target.value) || 1,
                    })
                  }
                >
                  <option value="1">1 Row (A1:F1)</option>
                  <option value="2">2 Rows (A1:F2)</option>
                  <option value="3">3 Rows (A1:F3)</option>
                  <option value="4">4 Rows (A1:F4)</option>
                  <option value="5">5 Rows (A1:F5)</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </Field.Root>

            <Field.Root
              gap={0}
              width={{ base: "100%", sm: "115px" }}
              flexShrink={0}
            >
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Horizontal
              </Field.Label>
              <NativeSelect.Root size="xs">
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={sheetHeaderStyle.horizontal ?? "center"}
                  onChange={(e) =>
                    updateSheetHeaderStyle({
                      horizontal: e.target.value,
                    })
                  }
                >
                  <option value="left">Left</option>
                  <option value="center">Center</option>
                  <option value="right">Right</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </Field.Root>

            <Field.Root
              gap={0}
              width={{ base: "100%", sm: "115px" }}
              flexShrink={0}
            >
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Vertical
              </Field.Label>
              <NativeSelect.Root size="xs">
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={sheetHeaderStyle.vertical ?? "center"}
                  onChange={(e) =>
                    updateSheetHeaderStyle({
                      vertical: e.target.value,
                    })
                  }
                >
                  <option value="top">Top</option>
                  <option value="center">Center</option>
                  <option value="bottom">Bottom</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </Field.Root>
          </Flex>

          {/* Row 2: Font Name, Font Size, Background, Text Color, Bold, Italic */}
          <Flex gap={2.5} align="flex-end" wrap="wrap">
            <Field.Root gap={0} flex={1} minW="180px">
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Font Name
              </Field.Label>
              <ExcelFontSelector
                value={sheetHeaderStyle.font_name}
                onChange={(val) => updateSheetHeaderStyle({ font_name: val })}
              />
            </Field.Root>

            <Field.Root
              gap={0}
              width={{ base: "100%", sm: "70px" }}
              flexShrink={0}
            >
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Font Size
              </Field.Label>
              <Input
                size="xs"
                h="28px"
                bg="white"
                type="number"
                value={sheetHeaderStyle.font_size ?? 16}
                onChange={(e) =>
                  updateSheetHeaderStyle({
                    font_size: parseInt(e.target.value) || 16,
                  })
                }
              />
            </Field.Root>

            <ColorPickerInput
              label="Background (HEX)"
              value={sheetHeaderStyle.fill}
              onChange={(hex) => updateSheetHeaderStyle({ fill: hex })}
              placeholder="2F5597"
              allowNoFill={true}
              width={{ base: "100%", sm: "145px" }}
            />

            <ColorPickerInput
              label="Text Color (HEX)"
              value={sheetHeaderStyle.font_color}
              onChange={(hex) => updateSheetHeaderStyle({ font_color: hex })}
              placeholder="FFFFFF"
              width={{ base: "100%", sm: "145px" }}
            />

            <HStack gap={3} h="28px" align="center" pl={1} flexShrink={0}>
              <Checkbox.Root
                size="xs"
                colorPalette="brand"
                checked={sheetHeaderStyle.bold ?? true}
                onCheckedChange={(details) =>
                  updateSheetHeaderStyle({ bold: !!details.checked })
                }
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label fontSize="xs">Bold</Checkbox.Label>
              </Checkbox.Root>

              <Checkbox.Root
                size="xs"
                colorPalette="brand"
                checked={sheetHeaderStyle.italic ?? false}
                onCheckedChange={(details) =>
                  updateSheetHeaderStyle({
                    italic: !!details.checked,
                  })
                }
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label fontSize="xs">Italic</Checkbox.Label>
              </Checkbox.Root>
            </HStack>
          </Flex>

          {/* Sheet Header Preview Below */}
          <Box
            p={2}
            bg="white"
            borderRadius="md"
            border="1px solid"
            borderColor="gray.200"
            mt={1}
          >
            <Text fontSize="10px" fontWeight="semibold" color="gray.500" mb={1}>
              Sheet Header Preview
            </Text>
            <Flex
              p={2}
              bg={getCssColor(sheetHeaderStyle.fill, "#2F5597")}
              color={getCssColor(sheetHeaderStyle.font_color, "#FFFFFF")}
              fontWeight={sheetHeaderStyle.bold ? "bold" : "normal"}
              fontStyle={sheetHeaderStyle.italic ? "italic" : "normal"}
              fontSize={`${sheetHeaderStyle.font_size ?? 16}px`}
              fontFamily={sheetHeaderStyle.font_name || "Calibri"}
              justifyContent={sheetHeaderStyle.horizontal || "center"}
              alignItems={getCssAlignItems(sheetHeaderStyle.vertical)}
              borderRadius="sm"
              border="1px solid"
              borderColor="gray.300"
              minHeight={`${Math.max(45, 24 * (options.sheet_header_row_span ?? 1))}px`}
              textAlign={sheetHeaderStyle.horizontal || "center"}
            >
              {options.sheet_header || tableName || "Report Title Header"}
            </Flex>
          </Box>
        </>
      )}
    </VStack>
  );
}
