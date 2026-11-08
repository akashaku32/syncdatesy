import { memo } from "react";

import { Button, Flex, Text } from "@chakra-ui/react";

import { DAY_CHIP_LABELS, DAY_CODES, type DayCode } from "./scheduleOptions";

interface DayOfWeekChipsProps {
  value: DayCode[];
  onChange: (_days: DayCode[]) => void;
  disabled?: boolean;
}

const DayOfWeekChips = ({
  value,
  onChange,
  disabled = false,
}: DayOfWeekChipsProps) => {
  const toggle = (code: DayCode) => {
    if (disabled) return;
    const selected = new Set(value);
    if (selected.has(code)) {
      selected.delete(code);
    } else {
      selected.add(code);
    }
    const ordered = DAY_CODES.filter((d) => selected.has(d));
    onChange(ordered);
  };

  return (
    <Flex direction="column" gap={2}>
      <Text fontSize="sm" fontWeight="semibold">
        On these days
      </Text>
      <Flex gap={2} wrap="wrap">
        {DAY_CODES.map((code, index) => {
          const isSelected = value.includes(code);
          return (
            <Button
              key={code}
              type="button"
              aria-pressed={isSelected}
              aria-label={code}
              disabled={disabled}
              onClick={() => toggle(code)}
              w="32px"
              h="32px"
              minW="32px"
              p={0}
              borderRadius="full"
              variant={isSelected ? "solid" : "outline"}
              colorPalette={isSelected ? "brand" : "gray"}
              fontSize="xs"
              fontWeight="semibold"
            >
              {DAY_CHIP_LABELS[index]}
            </Button>
          );
        })}
      </Flex>
    </Flex>
  );
};

export default memo(DayOfWeekChips);
