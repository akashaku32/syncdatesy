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
import { type BodyStyleSectionProps, getCssColor } from "../../helpers";

interface ExtendedBodyStyleSectionProps extends BodyStyleSectionProps {
  onClearBanding: () => void;
}

export default function BodyStyleSection({
  bodyStyle,
  updateBodyStyle,
  onClearBanding,
}: ExtendedBodyStyleSectionProps) {
  return (
    <VStack gap={2.5} align="stretch">
      {/* Row 1: Font Name, Font Size, Background, Text Color, Bold, Italic */}
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
            value={bodyStyle.font_name}
            onChange={(val) => updateBodyStyle({ font_name: val })}
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
            value={bodyStyle.font_size ?? 11}
            onChange={(e) =>
              updateBodyStyle({
                font_size: parseInt(e.target.value) || 11,
              })
            }
          />
        </Field.Root>

        <ColorPickerInput
          label="Background (HEX)"
          value={bodyStyle.fill}
          onChange={(hex) => updateBodyStyle({ fill: hex })}
          placeholder="FFFFFF"
          allowNoFill={true}
          width={{ base: "100%", sm: "145px" }}
        />

        <ColorPickerInput
          label="Text Color (HEX)"
          value={bodyStyle.font_color}
          onChange={(hex) => updateBodyStyle({ font_color: hex })}
          placeholder="000000"
          width={{ base: "100%", sm: "145px" }}
        />

        <HStack gap={3} h="28px" align="center" pl={1} flexShrink={0}>
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={bodyStyle.bold ?? false}
            onCheckedChange={(details) =>
              updateBodyStyle({ bold: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Bold</Checkbox.Label>
          </Checkbox.Root>

          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={bodyStyle.italic ?? false}
            onCheckedChange={(details) =>
              updateBodyStyle({ italic: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Italic</Checkbox.Label>
          </Checkbox.Root>
        </HStack>
      </Flex>

      {/* Row 2: Alternating Row Banding & Banding Properties */}
      <Flex gap={2.5} align="flex-end" wrap="wrap">
        <Box h="28px" display="flex" alignItems="center">
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={!!bodyStyle.banding_fill}
            onCheckedChange={(details) => {
              if (details.checked) {
                updateBodyStyle({
                  banding_fill: "F2F2F2",
                  banding_font_color: bodyStyle.font_color || "000000",
                  banding_frequency: 2,
                });
              } else {
                onClearBanding();
              }
            }}
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.700"
              whiteSpace="nowrap"
            >
              Alternating Row Banding
            </Checkbox.Label>
          </Checkbox.Root>
        </Box>

        {!!bodyStyle.banding_fill && (
          <>
            <ColorPickerInput
              label="Banding Fill (HEX)"
              value={bodyStyle.banding_fill}
              onChange={(hex) => updateBodyStyle({ banding_fill: hex })}
              placeholder="F2F2F2"
              width={{ base: "100%", sm: "145px" }}
            />

            <ColorPickerInput
              label="Banding Text (HEX)"
              value={bodyStyle.banding_font_color}
              onChange={(hex) => updateBodyStyle({ banding_font_color: hex })}
              placeholder="000000"
              width={{ base: "100%", sm: "145px" }}
            />

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
                Interval
              </Field.Label>
              <NativeSelect.Root size="xs">
                <NativeSelect.Field
                  bg="white"
                  h="28px"
                  value={bodyStyle.banding_frequency ?? 2}
                  onChange={(e) =>
                    updateBodyStyle({
                      banding_frequency: parseInt(e.target.value) || 2,
                    })
                  }
                >
                  <option value="1">Every Row</option>
                  <option value="2">Every 2nd</option>
                  <option value="3">Every 3rd</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </Field.Root>
          </>
        )}
      </Flex>

      {/* Body Preview */}
      <Box
        p={2}
        bg="white"
        borderRadius="md"
        border="1px solid"
        borderColor="gray.200"
        mt={1}
      >
        <Text fontSize="10px" fontWeight="semibold" color="gray.500" mb={1}>
          Body Rows Preview
        </Text>
        <VStack gap={1} align="stretch">
          <Flex
            p={1.5}
            bg={getCssColor(bodyStyle.fill, "#FFFFFF")}
            color={getCssColor(bodyStyle.font_color, "#000000")}
            fontWeight={bodyStyle.bold ? "bold" : "normal"}
            fontStyle={bodyStyle.italic ? "italic" : "normal"}
            fontSize={`${bodyStyle.font_size ?? 11}px`}
            fontFamily={bodyStyle.font_name || "Calibri"}
            borderRadius="sm"
            border="1px solid"
            borderColor="gray.200"
            minHeight="26px"
            align="center"
            px={2}
          >
            Row 1 (Base Row Style)
          </Flex>
          <Flex
            p={1.5}
            bg={
              bodyStyle.banding_fill
                ? getCssColor(bodyStyle.banding_fill, "#F2F2F2")
                : getCssColor(bodyStyle.fill, "#FFFFFF")
            }
            color={
              bodyStyle.banding_fill
                ? getCssColor(bodyStyle.banding_font_color, "#000000")
                : getCssColor(bodyStyle.font_color, "#000000")
            }
            fontWeight={bodyStyle.bold ? "bold" : "normal"}
            fontStyle={bodyStyle.italic ? "italic" : "normal"}
            fontSize={`${bodyStyle.font_size ?? 11}px`}
            fontFamily={bodyStyle.font_name || "Calibri"}
            borderRadius="sm"
            border="1px solid"
            borderColor="gray.200"
            minHeight="26px"
            align="center"
            px={2}
          >
            Row 2{" "}
            {bodyStyle.banding_fill
              ? "(Alternating Banding Style)"
              : "(Base Row Style)"}
          </Flex>
          <Flex
            p={1.5}
            bg={getCssColor(bodyStyle.fill, "#FFFFFF")}
            color={getCssColor(bodyStyle.font_color, "#000000")}
            fontWeight={bodyStyle.bold ? "bold" : "normal"}
            fontStyle={bodyStyle.italic ? "italic" : "normal"}
            fontSize={`${bodyStyle.font_size ?? 11}px`}
            fontFamily={bodyStyle.font_name || "Calibri"}
            borderRadius="sm"
            border="1px solid"
            borderColor="gray.200"
            minHeight="26px"
            align="center"
            px={2}
          >
            Row 3 (Base Row Style)
          </Flex>
        </VStack>
      </Box>
    </VStack>
  );
}
