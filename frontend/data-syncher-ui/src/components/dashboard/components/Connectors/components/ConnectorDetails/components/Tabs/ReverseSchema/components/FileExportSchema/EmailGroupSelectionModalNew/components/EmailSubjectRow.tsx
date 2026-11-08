import React from "react";

import { Box, Flex, IconButton, Input, Text } from "@chakra-ui/react";

import { LuCheck, LuPencil, LuX } from "react-icons/lu";

import MetadataTagSelector from "@/components/dashboard/components/AccountSettings/Email/components/MetadataTagSelector";

import { type ActiveSection, renderSubject } from "../helpers";

interface EmailSubjectRowProps {
  activeSection: ActiveSection;
  onSetActiveSection: (_section: ActiveSection) => void;
  isEditing: boolean;
  onStartEditing: () => void;
  tempSubject: string;
  onChangeTempSubject: (_val: string) => void;
  onSave: () => void;
  onCancel: () => void;
  subjectTemplate: string;
  tableName: string;
  destinationName?: string;
  resolvedPath?: string;
  connectionName?: string;
  companyName?: string;
  sourceDisplayName?: string;
}

export const EmailSubjectRow: React.FC<EmailSubjectRowProps> = ({
  activeSection,
  onSetActiveSection,
  isEditing,
  onStartEditing,
  tempSubject,
  onChangeTempSubject,
  onSave,
  onCancel,
  subjectTemplate,
  tableName,
  destinationName = "",
  resolvedPath,
  connectionName,
  companyName,
  sourceDisplayName,
}) => {
  const isSelected = activeSection === "subject";

  return (
    <Box
      border={isSelected ? "1.5px solid" : "1px solid transparent"}
      borderColor={isSelected ? "brand.500" : "gray.200"}
      bg={isSelected ? "brand.50/30" : "transparent"}
      borderRadius="md"
      p="4px"
      cursor="pointer"
      onClick={(e) => {
        e.stopPropagation();
        onSetActiveSection("subject");
      }}
    >
      {isEditing ? (
        <Box
          bg="white"
          p={1}
          borderRadius="md"
          borderWidth="1px"
          borderColor="brand.200"
          onClick={(e) => e.stopPropagation()}
        >
          <Flex gap={2} align="center">
            <Text
              fontSize="xs"
              fontWeight="semibold"
              color="gray.700"
              whiteSpace="nowrap"
            >
              Subject:
            </Text>
            <Input
              size="xs"
              value={tempSubject}
              onChange={(e) => onChangeTempSubject(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSave();
                if (e.key === "Escape") onCancel();
              }}
              placeholder="Enter subject template..."
              flex={1}
              autoFocus
              bg="white"
              color="gray.850"
              fontFamily="system-ui"
              borderColor="gray.300"
              _focus={{ borderColor: "brand.500" }}
              h="20px"
            />
            <IconButton
              aria-label="Save subject"
              size="xs"
              variant="ghost"
              colorPalette="green"
              onClick={onSave}
              h="18px"
              w="18px"
              minW="18px"
            >
              <LuCheck size={12} />
            </IconButton>
            <IconButton
              aria-label="Cancel editing"
              size="xs"
              variant="ghost"
              colorPalette="red"
              onClick={onCancel}
              h="18px"
              w="18px"
              minW="18px"
            >
              <LuX size={12} />
            </IconButton>
          </Flex>
          <Flex align="center" gap={1.5} mt={1} flexWrap="wrap">
            <Text fontSize="9px" color="gray.500" whiteSpace="nowrap">
              Insert:
            </Text>
            <MetadataTagSelector
              onSelectTag={(tag) =>
                onChangeTempSubject(tempSubject + " " + tag)
              }
            />
          </Flex>
        </Box>
      ) : (
        <Flex
          align="center"
          justify="space-between"
          gap={1.5}
          cursor="pointer"
          py="1px"
          px="3px"
          border="1px solid transparent"
          borderRadius="md"
          transition="all 0.2s"
          _hover={{
            bg: "brand.50/40",
            border: "1px dashed",
            borderColor: "brand.300",
            color: "brand.600",
          }}
          onClick={(e) => {
            e.stopPropagation();
            onStartEditing();
          }}
          title="Click to edit subject line template"
        >
          <Text
            fontSize="xs"
            lineHeight="short"
            flex={1}
            fontFamily="system-ui"
            color="gray.850"
          >
            <strong>Subject: </strong>
            {renderSubject(
              subjectTemplate,
              tableName,
              destinationName,
              resolvedPath,
              connectionName,
              companyName,
              sourceDisplayName,
            ) || (
              <span
                style={{
                  color: "#64748b",
                  fontStyle: "italic",
                  fontWeight: "normal",
                }}
              >
                Enter Subject...
              </span>
            )}
          </Text>
          <IconButton
            aria-label="Edit subject"
            size="xs"
            variant="ghost"
            colorPalette="brand"
            flexShrink={0}
            h="16px"
            w="16px"
            minW="16px"
          >
            <LuPencil size={9.5} />
          </IconButton>
        </Flex>
      )}
    </Box>
  );
};
