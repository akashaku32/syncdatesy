import { useEffect, useRef, useState } from "react";

import { Box, Button, Input, NativeSelect } from "@chakra-ui/react";

import { STANDARD_FONTS } from "../helpers";

interface ExcelFontSelectorProps {
  value: string | undefined;
  onChange: (_val: string) => void;
}

export default function ExcelFontSelector({
  value,
  onChange,
}: ExcelFontSelectorProps) {
  const currentVal = value || "Calibri";
  const isStandard = STANDARD_FONTS.includes(currentVal);
  const [isCustom, setIsCustom] = useState(!isStandard);
  const isTypingRef = useRef(false);

  useEffect(() => {
    if (isTypingRef.current) {
      isTypingRef.current = false;
      return;
    }
    const isValCustom = value ? !STANDARD_FONTS.includes(value) : false;
    if (isCustom !== isValCustom) {
      const timer = setTimeout(() => {
        setIsCustom(isValCustom);
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [value, isCustom]);

  return (
    <Box width="100%">
      {isCustom ? (
        <Box position="relative" width="100%">
          <Input
            size="xs"
            h="28px"
            bg="white"
            value={value ?? ""}
            onChange={(e) => {
              isTypingRef.current = true;
              onChange(e.target.value);
            }}
            placeholder="Type custom font name..."
            pr="32px"
          />
          <Button
            type="button"
            size="xs"
            variant="ghost"
            position="absolute"
            right="4px"
            top="50%"
            transform="translateY(-50%)"
            onClick={() => {
              setIsCustom(false);
              onChange("Calibri");
            }}
            color="gray.400"
            _hover={{ color: "gray.600" }}
            height="20px"
            minWidth="20px"
            px={0}
            zIndex={2}
          >
            ✕
          </Button>
        </Box>
      ) : (
        <NativeSelect.Root size="xs" width="100%">
          <NativeSelect.Field
            bg="white"
            h="28px"
            value={currentVal}
            onChange={(e) => {
              if (e.target.value === "__custom__") {
                setIsCustom(true);
                onChange("");
              } else {
                onChange(e.target.value);
              }
            }}
          >
            {STANDARD_FONTS.map((font) => (
              <option key={font} value={font}>
                {font}
              </option>
            ))}
            <option value="__custom__">Custom...</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
      )}
    </Box>
  );
}
