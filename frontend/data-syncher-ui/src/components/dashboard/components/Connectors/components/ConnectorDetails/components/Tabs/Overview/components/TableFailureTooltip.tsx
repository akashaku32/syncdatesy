import { type ReactNode } from "react";

import { Box, Flex, Text } from "@chakra-ui/react";

import { LuCopy } from "react-icons/lu";

import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";

const FALLBACK_FAILED = "Migration failed — no error detail was recorded.";

export function TableFailureTooltip({
  errorText,
  children,
}: {
  errorText: string;
  children: ReactNode;
}) {
  const detail = errorText.trim() || FALLBACK_FAILED;

  return (
    <Tooltip
      content={
        <Flex alignItems="flex-start" gap={2}>
          <Box
            bg="red.500"
            borderRadius="full"
            p={1}
            mt={0.5}
            minW="16px"
            h="16px"
            display="flex"
            alignItems="center"
            justifyContent="center"
          >
            <Text fontSize="xs" fontWeight="bold" lineHeight={1}>
              !
            </Text>
          </Box>
          <Text
            fontSize="xs"
            fontWeight="medium"
            flex={1}
            wordBreak="break-word"
          >
            {detail.startsWith("Error:") ? detail : `Error: ${detail}`}
          </Text>
          {errorText.trim() ? (
            <Box
              as="button"
              onClick={(e) => {
                e.stopPropagation();
                navigator.clipboard.writeText(errorText.trim());
                toaster.success({
                  title: "Copied to clipboard",
                  description: "Error message copied",
                });
              }}
              _hover={{
                color: "gray.300",
                bg: "whiteAlpha.200",
              }}
              cursor="pointer"
              p={1}
              borderRadius="md"
              transition="all 0.2s"
              title="Copy error message"
              color="white"
            >
              <LuCopy size={16} />
            </Box>
          ) : null}
        </Flex>
      }
      interactive
      closeOnPointerDown={false}
      openDelay={200}
      showArrow
      contentProps={{
        bg: "gray.800",
        color: "white",
        p: 3,
        borderRadius: "md",
        maxW: "520px",
      }}
    >
      <Box cursor="help" display="inline-flex" maxW="100%">
        {children}
      </Box>
    </Tooltip>
  );
}
