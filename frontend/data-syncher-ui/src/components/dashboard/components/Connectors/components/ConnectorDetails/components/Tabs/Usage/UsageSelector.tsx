import { Box, Flex } from "@chakra-ui/react";

interface UsageSelectorProps {
  selectedRange: string[];
  setSelectedRange: (_range: string[]) => void;
}

const UsageSelector = ({
  selectedRange,
  setSelectedRange,
}: UsageSelectorProps) => {
  const options = [
    { value: "current-month", label: "Last 7 Days" },
    { value: "all-time", label: "Last 12 Months" },
  ];

  return (
    <Flex direction="column" gap={1} width="fit-content">
      <Box fontWeight="bold" fontSize="lg" color="gray.700">
        Connection Usage Information
      </Box>
      <Flex bg="transparent" p="0" width="fit-content" gap="2">
        {options.map((option) => {
          const isSelected = selectedRange[0] === option.value;
          return (
            <Box
              key={option.value}
              as="button"
              onClick={() => setSelectedRange([option.value])}
              bg={isSelected ? "purple.600" : "white"}
              color={isSelected ? "white" : "purple.600"}
              border="1px solid"
              borderColor="purple.600"
              py={1.5}
              px={4}
              borderRadius="md"
              fontSize="sm"
              fontWeight="bold"
              shadow="sm"
              transition="all 0.2s"
              _hover={{
                bg: isSelected ? "purple.700" : "purple.50",
              }}
              cursor="pointer"
            >
              {option.label}
            </Box>
          );
        })}
      </Flex>
    </Flex>
  );
};

export default UsageSelector;
