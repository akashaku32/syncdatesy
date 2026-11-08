import { useRef } from "react";

import { Box, Button, Flex, Text } from "@chakra-ui/react";

import { FiFile, FiUpload } from "react-icons/fi";

type JsonFileChipProps = {
  fileName: string | null;
  size: number;
  mappingCount: number;
  parsing: boolean;
  disabled?: boolean;
  onFile: (_file: File) => void;
  onRemove: () => void;
};

const formatSize = (size: number) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};

const JsonFileChip = ({
  fileName,
  size,
  mappingCount,
  parsing,
  disabled,
  onFile,
  onRemove,
}: JsonFileChipProps) => {
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = () => {
    if (disabled) return;
    inputRef.current?.click();
  };

  return (
    <Box>
      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        hidden
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) onFile(file);
        }}
      />
      {fileName ? (
        <Flex
          borderWidth={1}
          borderColor="gray.200"
          borderRadius="md"
          px={3}
          py={2}
          align="center"
          gap={3}
          bg="gray.50"
        >
          <FiFile />
          <Box flex="1" minW={0}>
            <Text fontSize="sm" fontWeight="medium" truncate>
              {fileName}
            </Text>
            <Text fontSize="xs" color="gray.600">
              {formatSize(size)} · {mappingCount} mappings
            </Text>
          </Box>
          <Button
            size="xs"
            variant="outline"
            onClick={pick}
            disabled={disabled}
          >
            Replace
          </Button>
          <Button
            size="xs"
            variant="ghost"
            colorPalette="red"
            onClick={onRemove}
            disabled={disabled}
          >
            Remove
          </Button>
        </Flex>
      ) : (
        <Flex
          borderWidth={2}
          borderStyle="dashed"
          borderColor="gray.300"
          borderRadius="md"
          p={6}
          direction="column"
          align="center"
          gap={2}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            if (disabled) return;
            const file = event.dataTransfer.files[0];
            if (file) onFile(file);
          }}
        >
          <FiUpload />
          <Text fontSize="sm" color="gray.600">
            Drop mapping JSON here
          </Text>
          <Button
            size="sm"
            colorPalette="brand"
            variant="outline"
            onClick={pick}
            disabled={disabled}
          >
            Choose file
          </Button>
        </Flex>
      )}
      {parsing && (
        <Text fontSize="xs" color="brand.500" mt={1}>
          Parsing…
        </Text>
      )}
    </Box>
  );
};

export default JsonFileChip;
