import React from "react";

import { Box, Checkbox, Flex, Text, VStack } from "@chakra-ui/react";

import { LuChevronDown, LuX } from "react-icons/lu";

import { Tooltip } from "@/components/ui/tooltip";
import { type EmailGroup } from "@/types/emailGroups";

interface EmailRecipientSelectorProps {
  emailGroups: EmailGroup[];
  selectedIds: number[];
  isOpen: boolean;
  onToggleOpen: () => void;
  onClose: () => void;
  onToggleGroup: (_id: number) => void;
  onGoToEmailSettings: () => void;
  containerRef: React.RefObject<HTMLDivElement | null>;
}

export const EmailRecipientSelector: React.FC<EmailRecipientSelectorProps> = ({
  emailGroups,
  selectedIds,
  isOpen,
  onToggleOpen,
  onClose,
  onToggleGroup,
  onGoToEmailSettings,
  containerRef,
}) => {
  return (
    <Box
      position="relative"
      ref={containerRef}
      borderBottomWidth="1px"
      borderColor="gray.100"
      pb={1}
    >
      <Flex
        align="center"
        gap={1.5}
        fontSize="xs"
        cursor="pointer"
        onClick={onToggleOpen}
        py={0.5}
        _hover={{ bg: "gray.50/50" }}
        borderRadius="md"
        px={1}
      >
        <Text fontWeight="semibold" color="gray.500" flexShrink={0}>
          To:
        </Text>
        <Flex
          flex={1}
          flexWrap="wrap"
          gap={1}
          align="center"
          maxH="60px"
          overflowY="auto"
          css={{
            "&::-webkit-scrollbar": {
              display: "none",
            },
            msOverflowStyle: "none",
            scrollbarWidth: "none",
          }}
        >
          {selectedIds.length === 0 ? (
            <Text color="gray.400" fontStyle="italic" fontSize="10.5px">
              Click to select email groups...
            </Text>
          ) : (
            selectedIds.map((id) => {
              const g = emailGroups.find((group) => group.id === id);
              if (!g) return null;
              const emails =
                g.email_addresses && g.email_addresses.length > 0
                  ? g.email_addresses.join(", ")
                  : "No email addresses";
              return (
                <Tooltip key={id} content={emails} showArrow>
                  <Flex
                    align="center"
                    gap={1}
                    bg="purple.50"
                    borderWidth="1px"
                    borderColor="purple.200"
                    px={1.5}
                    py="1px"
                    borderRadius="sm"
                    fontSize="9.5px"
                    color="purple.800"
                    fontWeight="medium"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <span>{g.name}</span>
                    <Box
                      as="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleGroup(g.id);
                      }}
                      color="purple.500"
                      _hover={{ color: "purple.700" }}
                      display="inline-flex"
                      alignItems="center"
                      cursor="pointer"
                    >
                      <LuX size={9} />
                    </Box>
                  </Flex>
                </Tooltip>
              );
            })
          )}
        </Flex>
        <LuChevronDown
          size={10}
          color="#64748b"
          style={{ marginLeft: "auto", flexShrink: 0 }}
        />
      </Flex>

      {/* Dropdown list of groups */}
      {isOpen && (
        <Box
          position="absolute"
          top="100%"
          left="0"
          zIndex={1000}
          bg="white"
          boxShadow="0 4px 20px rgba(0,0,0,0.15)"
          p={2.5}
          borderRadius="lg"
          borderWidth="1px"
          borderColor="gray.200"
          width="240px"
          mt={1}
        >
          <Text fontSize="9px" fontWeight="bold" color="gray.600" mb={2}>
            SELECT EMAIL GROUPS
          </Text>
          {emailGroups.length === 0 ? (
            <VStack align="stretch" gap={1.5} py={2} px={1}>
              <Text fontSize="10.5px" color="gray.500" fontStyle="italic">
                No email groups configured.
              </Text>
              <Text
                as="span"
                fontSize="10.5px"
                color="brand.600"
                fontWeight="semibold"
                textDecoration="underline"
                cursor="pointer"
                _hover={{ color: "brand.700" }}
                onClick={() => {
                  onClose();
                  onGoToEmailSettings();
                }}
              >
                Go to Account Settings &gt; Communications to create one
              </Text>
            </VStack>
          ) : (
            <VStack align="stretch" gap={1.5} maxH="200px" overflowY="auto">
              {emailGroups.map((group) => {
                const isChecked = selectedIds.includes(group.id);
                return (
                  <Flex
                    key={group.id}
                    alignItems="center"
                    gap={2}
                    py={1}
                    px={1.5}
                    borderRadius="md"
                    cursor="pointer"
                    onClick={() => onToggleGroup(group.id)}
                    _hover={{ bg: "gray.50" }}
                  >
                    <Checkbox.Root
                      colorPalette="brand"
                      checked={isChecked}
                      onCheckedChange={() => onToggleGroup(group.id)}
                      size="sm"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Checkbox.HiddenInput />
                      <Checkbox.Control />
                    </Checkbox.Root>
                    <Text
                      fontSize="11px"
                      color="gray.700"
                      style={{
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        maxWidth: "100%",
                      }}
                    >
                      {group.name}
                    </Text>
                  </Flex>
                );
              })}
            </VStack>
          )}
        </Box>
      )}
    </Box>
  );
};
