import { useRef } from "react";

import { Badge, Box, Button, Flex, Text } from "@chakra-ui/react";

import { FiFile, FiUpload } from "react-icons/fi";

type ScriptFileChipProps = {
  fileName: string | null;
  size: number;
  lineCount: number;
  edited?: boolean;
  disabled?: boolean;
  onFile: (_file: File) => void;
  onRemove: () => void;
};

const formatSize = (size: number) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};

const ScriptFileChip = ({
  fileName,
  size,
  lineCount,
  edited = false,
  disabled,
  onFile,
  onRemove,
}: ScriptFileChipProps) => {
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
        accept=".py,text/x-python"
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
            <Flex align="center" gap={2} minW={0}>
              <Text fontSize="sm" fontWeight="medium" truncate>
                {fileName}
              </Text>
              {edited && (
                <Badge size="sm" colorPalette="orange" variant="subtle">
                  Edited
                </Badge>
              )}
            </Flex>
            <Text fontSize="xs" color="gray.600">
              {formatSize(size)}
              {lineCount > 0 ? ` · ${lineCount} lines` : ""}
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
          borderWidth={1}
          borderStyle="dashed"
          borderColor="gray.300"
          borderRadius="md"
          px={3}
          py={3}
          align="center"
          gap={3}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            if (disabled) return;
            const file = event.dataTransfer.files[0];
            if (file) onFile(file);
          }}
        >
          <FiUpload color="var(--chakra-colors-gray-500)" />
          <Text fontSize="sm" color="gray.600" flex="1">
            Upload a Python transform script
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
    </Box>
  );
};

export default ScriptFileChip;
