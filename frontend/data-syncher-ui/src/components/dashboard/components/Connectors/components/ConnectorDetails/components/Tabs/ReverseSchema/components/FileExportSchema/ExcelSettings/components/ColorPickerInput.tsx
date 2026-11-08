import { Field, HStack, Input } from "@chakra-ui/react";

import ColorPicker from "@/components/shared/ColorPicker";

interface ColorPickerInputProps {
  label: string;
  value: string | undefined;
  onChange: (_hex: string) => void;
  placeholder?: string;
  allowNoFill?: boolean;
  width?: Record<string, string> | string;
  flex?: number | string;
  minW?: string;
}

export default function ColorPickerInput({
  label,
  value,
  onChange,
  placeholder = "FFFFFF",
  allowNoFill = false,
  width,
  flex,
  minW,
}: ColorPickerInputProps) {
  return (
    <Field.Root gap={0} width={width} flex={flex} minW={minW} flexShrink={0}>
      <Field.Label
        fontSize="xs"
        fontWeight="semibold"
        color="gray.600"
        mb={0.5}
      >
        {label}
      </Field.Label>
      <HStack gap={1}>
        <ColorPicker
          value={value}
          onChange={onChange}
          w="16px"
          h="16px"
          borderRadius="sm"
          allowNoFill={allowNoFill}
        />
        <Input
          size="xs"
          h="28px"
          bg="white"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
        />
      </HStack>
    </Field.Root>
  );
}
