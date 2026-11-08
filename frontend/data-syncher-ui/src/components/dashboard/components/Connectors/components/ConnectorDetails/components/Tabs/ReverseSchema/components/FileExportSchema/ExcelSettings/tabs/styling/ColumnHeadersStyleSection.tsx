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
  type ColumnHeadersStyleSectionProps,
  getCssAlignItems,
  getCssColor,
} from "../../helpers";

interface ExtendedColumnHeaderProps extends ColumnHeadersStyleSectionProps {
  sheetName?: string;
}

export default function ColumnHeadersStyleSection({
  headerStyle,
  sheetName,
  updateHeaderStyle,
}: ExtendedColumnHeaderProps) {
  return (
    <VStack gap={2.5} align="stretch">
      {/* Row 1: Font Name, Font Size, Background, Text Color */}
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
            value={headerStyle.font_name}
            onChange={(val) => updateHeaderStyle({ font_name: val })}
          />
        </Field.Root>

        <Field.Root gap={0} width={{ base: "100%", sm: "70px" }} flexShrink={0}>
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
            value={headerStyle.font_size ?? 11}
            onChange={(e) =>
              updateHeaderStyle({
                font_size: parseInt(e.target.value) || 11,
              })
            }
          />
        </Field.Root>

        <ColorPickerInput
          label="Background (HEX)"
          value={headerStyle.fill}
          onChange={(hex) => updateHeaderStyle({ fill: hex })}
          placeholder="1F4E78"
          allowNoFill={true}
          width={{ base: "100%", sm: "145px" }}
        />

        <ColorPickerInput
          label="Text Color (HEX)"
          value={headerStyle.font_color}
          onChange={(hex) => updateHeaderStyle({ font_color: hex })}
          placeholder="FFFFFF"
          width={{ base: "100%", sm: "145px" }}
        />
      </Flex>

      {/* Row 2: Horizontal, Vertical, Bold, Italic, Wrap Text */}
      <Flex gap={2.5} align="flex-end" wrap="wrap">
        <Field.Root
          gap={0}
          width={{ base: "100%", sm: "120px" }}
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
              value={headerStyle.horizontal ?? "center"}
              onChange={(e) =>
                updateHeaderStyle({ horizontal: e.target.value })
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
          width={{ base: "100%", sm: "120px" }}
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
              value={headerStyle.vertical ?? "center"}
              onChange={(e) => updateHeaderStyle({ vertical: e.target.value })}
            >
              <option value="top">Top</option>
              <option value="center">Center</option>
              <option value="bottom">Bottom</option>
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>

        <HStack gap={3} h="28px" align="center" pl={1} flexShrink={0}>
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={headerStyle.bold ?? true}
            onCheckedChange={(details) =>
              updateHeaderStyle({ bold: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Bold</Checkbox.Label>
          </Checkbox.Root>

          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={headerStyle.italic ?? false}
            onCheckedChange={(details) =>
              updateHeaderStyle({ italic: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Italic</Checkbox.Label>
          </Checkbox.Root>

          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={headerStyle.wrap_text ?? true}
            onCheckedChange={(details) =>
              updateHeaderStyle({ wrap_text: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Wrap Text</Checkbox.Label>
          </Checkbox.Root>
        </HStack>
      </Flex>

      {/* Header Cell Preview */}
      <Box
        p={2}
        bg="white"
        borderRadius="md"
        border="1px solid"
        borderColor="gray.200"
        mt={1}
      >
        <Text fontSize="10px" fontWeight="semibold" color="gray.500" mb={1}>
          Header Cell Preview
        </Text>
        <Flex
          p={2}
          bg={getCssColor(headerStyle.fill, "#1F4E78")}
          color={getCssColor(headerStyle.font_color, "#FFFFFF")}
          fontWeight={headerStyle.bold ? "bold" : "normal"}
          fontStyle={headerStyle.italic ? "italic" : "normal"}
          fontSize={`${headerStyle.font_size ?? 11}px`}
          fontFamily={headerStyle.font_name || "Calibri"}
          justifyContent={headerStyle.horizontal || "center"}
          alignItems={getCssAlignItems(headerStyle.vertical)}
          borderRadius="sm"
          border="1px solid"
          borderColor="gray.300"
          minHeight="40px"
          textAlign={headerStyle.horizontal || "center"}
        >
          {sheetName || "Sheet1"} Header Column
        </Flex>
      </Box>
    </VStack>
  );
}
