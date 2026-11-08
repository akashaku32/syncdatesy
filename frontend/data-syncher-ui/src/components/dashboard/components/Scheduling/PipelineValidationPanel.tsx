import { Box, Flex, IconButton, List, Text } from "@chakra-ui/react";

import { MdClose } from "react-icons/md";

import { type PipelineValidationResult } from "@/types/pipeline";

type PipelineValidationPanelProps = {
  result: PipelineValidationResult;
  stale?: boolean;
  onDismiss?: () => void;
};

const DismissButton = ({
  onDismiss,
  color,
}: {
  onDismiss?: () => void;
  color: string;
}) => {
  if (!onDismiss) return null;
  return (
    <IconButton
      aria-label="Dismiss"
      size="2xs"
      variant="ghost"
      color={color}
      onClick={onDismiss}
      flexShrink={0}
      _hover={{ bg: "blackAlpha.100" }}
    >
      <MdClose />
    </IconButton>
  );
};

const PipelineValidationPanel = ({
  result,
  stale = false,
  onDismiss,
}: PipelineValidationPanelProps) => {
  const { valid, errors, warnings } = result;

  if (stale) {
    return (
      <Flex
        px={3}
        py={2}
        bg="orange.50"
        borderBottomWidth={1}
        borderColor="orange.200"
        align="center"
        justify="space-between"
        gap={2}
      >
        <Text fontSize="sm" color="orange.800" fontWeight="medium">
          Unpublished canvas changes — validate to update the scheduled flow.
        </Text>
        <DismissButton onDismiss={onDismiss} color="orange.700" />
      </Flex>
    );
  }

  if (valid) {
    return (
      <Box
        px={3}
        py={2}
        bg="green.50"
        borderBottomWidth={1}
        borderColor="green.200"
      >
        <Flex align="center" justify="space-between" gap={2}>
          <Text fontSize="sm" color="green.800" fontWeight="medium">
            Published — schedule will run this flow.
          </Text>
          <DismissButton onDismiss={onDismiss} color="green.700" />
        </Flex>
        {warnings.length > 0 && (
          <List.Root fontSize="sm" color="orange.800" mt={1} ps={4} gap={0.5}>
            {warnings.map((warning) => (
              <List.Item key={warning}>{warning}</List.Item>
            ))}
          </List.Root>
        )}
      </Box>
    );
  }

  return (
    <Box px={3} py={2} bg="red.50" borderBottomWidth={1} borderColor="red.200">
      <Flex align="center" justify="space-between" gap={2} mb={1}>
        <Text fontSize="sm" color="red.800" fontWeight="semibold">
          Validation failed
        </Text>
        <DismissButton onDismiss={onDismiss} color="red.700" />
      </Flex>
      <List.Root
        as="ul"
        fontSize="sm"
        color="red.700"
        ps={4}
        gap={0.5}
        listStyleType="disc"
      >
        {errors.map((error) => (
          <List.Item key={error} ml={1}>
            {error}
          </List.Item>
        ))}
      </List.Root>
      {result.has_published_graph && (
        <Text fontSize="xs" color="red.700" mt={1.5}>
          Schedule will keep running the last published flow until you fix and
          validate again.
        </Text>
      )}
      {warnings.length > 0 && (
        <List.Root fontSize="xs" color="orange.800" ps={4} gap={0.5} mt={1.5}>
          {warnings.map((warning) => (
            <List.Item key={warning}>{warning}</List.Item>
          ))}
        </List.Root>
      )}
    </Box>
  );
};

export default PipelineValidationPanel;
