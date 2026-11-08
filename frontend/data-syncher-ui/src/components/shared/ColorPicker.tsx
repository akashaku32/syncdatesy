import { useEffect, useRef, useState } from "react";

import { Box, Flex, Grid, Text, VStack } from "@chakra-ui/react";

import { MdColorLens } from "react-icons/md";

export interface ColorPickerProps {
  value: string | undefined;
  onChange: (_hex: string) => void;
  borderRadius?: string;
  w?: string;
  h?: string;
  allowNoFill?: boolean;
}

export type ExcelColorPickerProps = ColorPickerProps;

// Shader helper function to calculate tints/shades
const shadeColor = (color: string, percent: number): string => {
  let cleanColor = color.replace("#", "");
  if (cleanColor.length === 3) {
    cleanColor = cleanColor
      .split("")
      .map((c) => c + c)
      .join("");
  }
  let R = parseInt(cleanColor.substring(0, 2), 16);
  let G = parseInt(cleanColor.substring(2, 4), 16);
  let B = parseInt(cleanColor.substring(4, 6), 16);

  if (percent > 0) {
    // Tint (lighter)
    R = Math.round(R + (255 - R) * percent);
    G = Math.round(G + (255 - G) * percent);
    B = Math.round(B + (255 - B) * percent);
  } else {
    // Shade (darker)
    R = Math.round(R * (1 + percent));
    G = Math.round(G * (1 + percent));
    B = Math.round(B * (1 + percent));
  }

  const rHex = Math.min(255, Math.max(0, R)).toString(16).padStart(2, "0");
  const gHex = Math.min(255, Math.max(0, G)).toString(16).padStart(2, "0");
  const bHex = Math.min(255, Math.max(0, B)).toString(16).padStart(2, "0");

  return `${rHex}${gHex}${bHex}`.toUpperCase();
};

// Standard Theme Base Colors (10 columns)
const THEME_BASE_COLORS = [
  "FFFFFF", // White
  "000000", // Black
  "E7E6E6", // Grayish Tan
  "1F4E78", // Navy Blue
  "4472C4", // Steel Blue
  "C00000", // Crimson Red
  "70AD47", // Olive Green
  "7030A0", // Purple
  "00B0F0", // Teal/Aqua
  "FFC000", // Orange
];

// High contrast Base Colors
const HIGH_CONTRAST_BASE_COLORS = [
  "FFFFFF",
  "000000",
  "FFFF00", // Bright Yellow
  "00FFFF", // Cyan
  "FF00FF", // Magenta
  "FF0000", // Red
  "00FF00", // Lime Green
  "0000FF", // Blue
  "800000", // Maroon
  "008080", // Teal
];

// Standard Colors row
const STANDARD_COLORS = [
  "C00000", // Dark Red
  "FF0000", // Red
  "FFC000", // Orange
  "FFFF00", // Yellow
  "92D050", // Light Green
  "00B050", // Green
  "00B0F0", // Light Blue
  "0070C0", // Blue
  "002060", // Dark Blue
  "7030A0", // Purple
];

const getCssColor = (hex: string | undefined, defaultColor: string): string => {
  if (!hex) return defaultColor;
  const clean = hex.trim();
  if (clean.startsWith("#")) return clean;
  if (/^[0-9A-Fa-f]{3,6}$/.test(clean)) return `#${clean}`;
  return clean;
};

export default function ColorPicker({
  value,
  onChange,
  borderRadius = "sm",
  w = "20px",
  h = "20px",
  allowNoFill = false,
}: ColorPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isHighContrast, setIsHighContrast] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Close popover when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const baseList = isHighContrast
    ? HIGH_CONTRAST_BASE_COLORS
    : THEME_BASE_COLORS;

  // Build the 10x6 Theme Color Matrix
  // Row 0: Base colors
  // Row 1-5: Shades
  const matrix: string[][] = Array.from({ length: 6 }, () => []);

  // Row 0: Base
  matrix[0] = baseList;

  // Shade rows: row 1 to 5
  // Columns 1 and 2 (indexes 0 and 1) and Column 3 (index 2) have hardcoded gray scales to match Excel
  const grayColumn1 = ["F2F2F2", "D9D9D9", "BFBFBF", "A6A6A6", "7F7F7F"];
  const grayColumn2 = ["7F7F7F", "595959", "3F3F3F", "262626", "0D0D0D"];
  const grayColumn3 = ["F2F2F2", "D9D9D9", "BFBFBF", "A6A6A6", "7F7F7F"];

  const shadePercentages = [0.8, 0.6, 0.4, -0.25, -0.5];

  for (let r = 1; r <= 5; r++) {
    const percent = shadePercentages[r - 1];
    for (let c = 0; c < 10; c++) {
      if (c === 0) {
        matrix[r].push(grayColumn1[r - 1]);
      } else if (c === 1) {
        matrix[r].push(grayColumn2[r - 1]);
      } else if (c === 2) {
        matrix[r].push(grayColumn3[r - 1]);
      } else {
        matrix[r].push(shadeColor(baseList[c], percent));
      }
    }
  }

  const handleSelectColor = (hex: string) => {
    onChange(hex.replace("#", ""));
    setIsOpen(false);
  };

  const handleMoreColorsClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    fileInputRef.current?.click();
  };

  const handleCustomColorChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value.replace("#", ""));
  };

  const activeColorHex = getCssColor(value, "#FFFFFF");

  const getValidPickerHex = (hex: string | undefined): string => {
    if (!hex) return "#ffffff";
    let clean = hex.trim().replace("#", "");
    if (clean.length === 3) {
      clean = clean
        .split("")
        .map((c) => c + c)
        .join("");
    }
    if (/^[0-9A-Fa-f]{6}$/.test(clean)) {
      return `#${clean.toLowerCase()}`;
    }
    return "#000000";
  };

  return (
    <Box
      position="relative"
      ref={containerRef}
      style={{ display: "inline-block" }}
    >
      {/* Hidden native color input - persistently mounted so native picker never aborts during interaction */}
      <input
        ref={fileInputRef}
        type="color"
        value={getValidPickerHex(value)}
        onChange={handleCustomColorChange}
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 0,
          height: 0,
          opacity: 0,
          pointerEvents: "none",
        }}
        tabIndex={-1}
        aria-hidden="true"
      />
      {/* Trigger: Color preview box */}
      <Box
        w={w}
        h={h}
        borderRadius={borderRadius}
        border="1px solid"
        borderColor="gray.300"
        bg={activeColorHex}
        cursor="pointer"
        onClick={() => setIsOpen(!isOpen)}
        title={value ? `#${value}` : "No Fill"}
        _hover={{ transform: "scale(1.05)", shadow: "xs" }}
        transition="all 0.15s"
        position="relative"
        overflow="hidden"
      >
        {!value && (
          // Diagonal red line for No Fill
          <Box
            position="absolute"
            top="0"
            left="0"
            w="200%"
            h="1px"
            bg="red.500"
            transform="rotate(45deg)"
            transformOrigin="top left"
          />
        )}
      </Box>

      {/* Popover container */}
      {isOpen && (
        <Box
          position="absolute"
          top={`calc(${h} + 6px)`}
          left="0"
          zIndex={9999}
          bg="white"
          boxShadow="lg"
          border="1px solid"
          borderColor="gray.200"
          borderRadius="md"
          p={3.5}
          minW="240px"
          w="252px"
        >
          {/* Header Row: High-contrast toggle */}
          <Flex
            align="center"
            justify="space-between"
            mb={2}
            pb={1.5}
            borderBottom="1px solid"
            borderColor="gray.100"
          >
            <Text fontSize="xs" fontWeight="semibold" color="gray.600">
              High-contrast only
            </Text>
            {/* Simple custom toggle switch */}
            <Box
              w="32px"
              h="18px"
              bg={isHighContrast ? "brand.500" : "gray.300"}
              borderRadius="full"
              p="2px"
              cursor="pointer"
              onClick={(e) => {
                e.stopPropagation();
                setIsHighContrast(!isHighContrast);
              }}
              transition="background-color 0.2s"
            >
              <Box
                w="14px"
                h="14px"
                bg="white"
                borderRadius="full"
                shadow="sm"
                transform={
                  isHighContrast ? "translateX(14px)" : "translateX(0)"
                }
                transition="transform 0.2s"
              />
            </Box>
          </Flex>

          {/* Theme Colors Section */}
          <VStack align="stretch" gap={1.5} mb={3}>
            <Text
              fontSize="11px"
              fontWeight="bold"
              color="gray.700"
              letterSpacing="wide"
            >
              Theme Colors
            </Text>
            {/* Grid layout for base colors + shades (10 columns, 6 rows) */}
            <Flex direction="column" gap="2px">
              {matrix.map((row, rIdx) => (
                <Grid
                  key={rIdx}
                  templateColumns="repeat(10, 1fr)"
                  gap="2px"
                  mb={rIdx === 0 ? "3px" : "0"}
                >
                  {row.map((hex, cIdx) => {
                    const cleanHex = hex.replace("#", "");
                    const isSelected =
                      value?.toUpperCase() === cleanHex.toUpperCase();
                    return (
                      <Box
                        key={cIdx}
                        w="18px"
                        h="18px"
                        bg={`#${cleanHex}`}
                        border="1px solid"
                        borderColor={isSelected ? "brand.500" : "gray.200"}
                        cursor="pointer"
                        _hover={{
                          transform: "scale(1.15)",
                          zIndex: 1,
                          shadow: "xs",
                        }}
                        transition="all 0.1s"
                        onClick={() => handleSelectColor(cleanHex)}
                        title={`#${cleanHex}`}
                      />
                    );
                  })}
                </Grid>
              ))}
            </Flex>
          </VStack>

          {/* Standard Colors Section */}
          <VStack align="stretch" gap={1.5} mb={3}>
            <Text
              fontSize="11px"
              fontWeight="bold"
              color="gray.700"
              letterSpacing="wide"
            >
              Standard Colors
            </Text>
            <Grid templateColumns="repeat(10, 1fr)" gap="2px">
              {STANDARD_COLORS.map((hex, idx) => {
                const isSelected = value?.toUpperCase() === hex.toUpperCase();
                return (
                  <Box
                    key={idx}
                    w="18px"
                    h="18px"
                    bg={`#${hex}`}
                    border="1px solid"
                    borderColor={isSelected ? "brand.500" : "gray.200"}
                    cursor="pointer"
                    _hover={{
                      transform: "scale(1.15)",
                      zIndex: 1,
                      shadow: "xs",
                    }}
                    transition="all 0.1s"
                    onClick={() => handleSelectColor(hex)}
                    title={`#${hex}`}
                  />
                );
              })}
            </Grid>
          </VStack>

          {/* Bottom Controls: No Fill & More Colors */}
          <VStack
            align="stretch"
            gap={1}
            borderTop="1px solid"
            borderColor="gray.100"
            pt={2}
          >
            {allowNoFill && (
              <Flex
                align="center"
                gap={2}
                py={1.5}
                px={2}
                cursor="pointer"
                _hover={{ bg: "gray.50" }}
                borderRadius="sm"
                onClick={() => {
                  onChange("");
                  setIsOpen(false);
                }}
              >
                <Box
                  w="16px"
                  h="16px"
                  border="1px dashed"
                  borderColor="gray.400"
                  position="relative"
                  borderRadius="2px"
                  overflow="hidden"
                >
                  <Box
                    position="absolute"
                    top="0"
                    left="0"
                    w="200%"
                    h="1px"
                    bg="red.500"
                    transform="rotate(45deg)"
                    transformOrigin="top left"
                  />
                </Box>
                <Text fontSize="xs" fontWeight="medium" color="gray.700">
                  No Fill
                </Text>
              </Flex>
            )}

            <Flex
              align="center"
              gap={2}
              py={1.5}
              px={2}
              cursor="pointer"
              _hover={{ bg: "gray.50" }}
              borderRadius="sm"
              onClick={handleMoreColorsClick}
            >
              <Box color="gray.500" fontSize="14px">
                <MdColorLens />
              </Box>
              <Text fontSize="xs" fontWeight="medium" color="gray.700">
                More Colors...
              </Text>
            </Flex>
          </VStack>
        </Box>
      )}
    </Box>
  );
}

export { ColorPicker };
