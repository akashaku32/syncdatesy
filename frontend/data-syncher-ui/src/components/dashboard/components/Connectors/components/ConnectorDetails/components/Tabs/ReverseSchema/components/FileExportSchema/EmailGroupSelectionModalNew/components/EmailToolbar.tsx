import React from "react";

import {
  Box,
  Button,
  Checkbox,
  Flex,
  IconButton,
  Text,
  VStack,
} from "@chakra-ui/react";

import { LuBold, LuChevronDown, LuItalic, LuX } from "react-icons/lu";

import ColorPicker from "@/components/shared/ColorPicker";
import { type EmailTemplate } from "@/types/emailTemplates";

import {
  type ActiveSection,
  BODY_FIELDS_OPTIONS,
  type SectionStyleState,
  getPathLabel,
} from "../helpers";

interface EmailToolbarProps {
  activeSection: ActiveSection;
  activeStyle: SectionStyleState;
  onUpdateActiveStyle: (_patch: Partial<SectionStyleState>) => void;
  destinationName?: string;
  pathLabel?: string | null;
  selectedBodyFields: string[];
  onToggleBodyField: (_id: string) => void;
  isMetadataOpen: boolean;
  onToggleMetadataOpen: () => void;
  metadataRef: React.RefObject<HTMLDivElement | null>;
  savedTemplates: EmailTemplate[];
  selectedTemplateId: number | string | null;
  isTemplatesOpen: boolean;
  onToggleTemplatesOpen: () => void;
  templatesRef: React.RefObject<HTMLDivElement | null>;
  onApplyTemplate: (_t: EmailTemplate) => void;
  onResetTemplate: () => void;
  onNavigateToTemplates: () => void;
}

export const EmailToolbar: React.FC<EmailToolbarProps> = ({
  activeSection,
  activeStyle,
  onUpdateActiveStyle,
  destinationName,
  pathLabel,
  selectedBodyFields,
  onToggleBodyField,
  isMetadataOpen,
  onToggleMetadataOpen,
  metadataRef,
  savedTemplates,
  selectedTemplateId,
  isTemplatesOpen,
  onToggleTemplatesOpen,
  templatesRef,
  onApplyTemplate,
  onResetTemplate,
  onNavigateToTemplates,
}) => {
  const isSubject = activeSection === "subject";
  const selectedTemplate = savedTemplates.find(
    (t) => String(t.id) === String(selectedTemplateId),
  );

  return (
    <Flex
      gap={2}
      align="center"
      justify="space-between"
      p="3px"
      px="8px"
      bg="gray.50"
      borderRadius="lg"
      borderWidth="1px"
      borderColor="gray.200"
      boxShadow="xs"
    >
      {/* Left: Text Styling Actions */}
      <Flex align="center" gap={1.5} wrap="wrap">
        {/* Bold / Italic */}
        <Flex
          gap={3}
          bg="white"
          p="1px"
          borderRadius="md"
          borderWidth="1px"
          borderColor="gray.200"
        >
          <IconButton
            size="xs"
            variant={activeStyle?.bold ? "solid" : "ghost"}
            colorPalette={activeStyle?.bold ? "brand" : "gray"}
            onClick={() => onUpdateActiveStyle({ bold: !activeStyle?.bold })}
            h="18px"
            w="18px"
            minW="18px"
            disabled={isSubject}
            aria-label="Bold text"
          >
            <LuBold size={9.5} />
          </IconButton>
          <IconButton
            size="xs"
            variant={activeStyle?.italic ? "solid" : "ghost"}
            colorPalette={activeStyle?.italic ? "brand" : "gray"}
            onClick={() =>
              onUpdateActiveStyle({ italic: !activeStyle?.italic })
            }
            h="18px"
            w="18px"
            minW="18px"
            disabled={isSubject}
            aria-label="Italic text"
          >
            <LuItalic size={9.5} />
          </IconButton>
        </Flex>

        <Box w="1px" h="10px" bg="gray.300" mx={0.5} />

        {/* Font selector */}
        <select
          value={activeStyle?.fontFamily || "system-ui"}
          onChange={(e) => onUpdateActiveStyle({ fontFamily: e.target.value })}
          disabled={isSubject}
          style={{
            fontSize: "9.5px",
            fontWeight: "500",
            height: "18px",
            outline: "none",
            border: "1px solid #e2e8f0",
            borderRadius: "6px",
            paddingLeft: "4px",
            paddingRight: "4px",
            backgroundColor: "white",
            color: "#334155",
            cursor: isSubject ? "not-allowed" : "pointer",
            opacity: isSubject ? 0.6 : 1,
          }}
        >
          <option value="system-ui">Sans-serif</option>
          <option value="Arial">Arial</option>
          <option value="Verdana">Verdana</option>
          <option value="Tahoma">Tahoma</option>
          <option value="Georgia">Georgia</option>
        </select>

        {/* Font Size selector */}
        <select
          value={activeStyle?.fontSize || "12px"}
          onChange={(e) => onUpdateActiveStyle({ fontSize: e.target.value })}
          disabled={isSubject}
          style={{
            fontSize: "9.5px",
            fontWeight: "500",
            height: "18px",
            outline: "none",
            border: "1px solid #e2e8f0",
            borderRadius: "6px",
            paddingLeft: "4px",
            paddingRight: "4px",
            backgroundColor: "white",
            color: "#334155",
            cursor: isSubject ? "not-allowed" : "pointer",
            opacity: isSubject ? 0.6 : 1,
          }}
        >
          <option value="11px">11px</option>
          <option value="12px">12px</option>
          <option value="14px">14px</option>
          <option value="16px">16px</option>
          <option value="18px">18px</option>
        </select>

        <Box w="1px" h="10px" bg="gray.300" mx={0.5} />

        {/* Color Picker using shared ColorPicker */}
        <Box
          opacity={isSubject ? 0.5 : 1}
          pointerEvents={isSubject ? "none" : "auto"}
        >
          <ColorPicker
            value={activeStyle?.color || "#000000"}
            onChange={(hex) =>
              onUpdateActiveStyle({
                color: hex.startsWith("#") ? hex : `#${hex}`,
              })
            }
            w="18px"
            h="18px"
            borderRadius="md"
          />
        </Box>
      </Flex>

      {/* Right: Metadata & Templates dropdown popovers */}
      <Flex align="center" gap={1.5}>
        <Box position="relative" ref={metadataRef}>
          <Flex
            as="button"
            align="center"
            gap={1}
            onClick={onToggleMetadataOpen}
            h="18px"
            px={3}
            bg="white"
            borderWidth="1px"
            borderColor="gray.200"
            borderRadius="md"
            cursor="pointer"
            boxShadow="xs"
            _hover={{ bg: "gray.50" }}
            transition="all 0.15s"
          >
            <span
              style={{
                fontSize: "9.5px",
                fontWeight: 600,
                color: "#475569",
              }}
            >
              Metadata
            </span>
            <LuChevronDown size={8} color="#64748b" />
          </Flex>
          {isMetadataOpen && (
            <Box
              position="absolute"
              top="22px"
              right="0"
              zIndex={999}
              bg="white"
              boxShadow="0 4px 20px rgba(0,0,0,0.15)"
              p={3}
              borderRadius="lg"
              borderWidth="1px"
              borderColor="gray.200"
              width="210px"
            >
              <Text fontSize="10px" fontWeight="bold" color="gray.600" mb={2}>
                Select Fields to Include
              </Text>
              <VStack align="stretch" gap={1.5} maxH="220px" overflowY="auto">
                {BODY_FIELDS_OPTIONS.map((field) => {
                  const isFieldChecked = selectedBodyFields.includes(field.id);
                  const fieldLabel =
                    field.id === "path"
                      ? getPathLabel(destinationName, pathLabel)
                      : field.label;
                  return (
                    <Flex
                      key={field.id}
                      alignItems="center"
                      gap={2}
                      py={1}
                      px={1.5}
                      borderRadius="md"
                      cursor="pointer"
                      onClick={() => onToggleBodyField(field.id)}
                      _hover={{ bg: "gray.50" }}
                    >
                      <Checkbox.Root
                        colorPalette="brand"
                        checked={isFieldChecked}
                        onCheckedChange={() => onToggleBodyField(field.id)}
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
                        {fieldLabel}
                      </Text>
                    </Flex>
                  );
                })}
              </VStack>
            </Box>
          )}
        </Box>

        {/* Templates Dropdown Popover */}
        <Flex align="center" gap={1.5}>
          <Box position="relative" ref={templatesRef}>
            <Button
              size="xs"
              colorPalette="purple"
              variant="solid"
              h="18px"
              px={2.5}
              fontSize="9.5px"
              fontWeight="bold"
              onClick={onToggleTemplatesOpen}
              display="flex"
              alignItems="center"
              gap={1}
              borderRadius="md"
            >
              <span>Templates</span>
              <LuChevronDown size={8} color="white" />
            </Button>

            {isTemplatesOpen && (
              <Box
                position="absolute"
                top="22px"
                right="0"
                zIndex={1000}
                bg="white"
                boxShadow="0 6px 24px rgba(0,0,0,0.18)"
                p={2}
                borderRadius="lg"
                borderWidth="1px"
                borderColor="gray.200"
                width="230px"
              >
                <Flex justify="space-between" align="center" mb={1.5} px={1}>
                  <Text fontSize="10px" fontWeight="bold" color="gray.700">
                    Saved Templates
                  </Text>
                  <Text
                    fontSize="8.5px"
                    color="purple.600"
                    cursor="pointer"
                    fontWeight="semibold"
                    onClick={onNavigateToTemplates}
                  >
                    Manage &rarr;
                  </Text>
                </Flex>
                <VStack align="stretch" gap={1} maxH="220px" overflowY="auto">
                  {/* Default Template Reset Option */}
                  <Box
                    as="button"
                    onClick={onResetTemplate}
                    p={1.5}
                    borderRadius="md"
                    borderWidth="1px"
                    borderColor="gray.200"
                    bg="gray.50"
                    textAlign="left"
                    cursor="pointer"
                    _hover={{
                      bg: "purple.50",
                      borderColor: "purple.300",
                    }}
                    transition="all 0.15s"
                  >
                    <Flex align="center" justify="space-between" mb={0.5}>
                      <Flex align="center" gap={1.5}>
                        <Box w="7px" h="7px" borderRadius="full" bg="#6e2fd5" />
                        <Text
                          fontSize="10.5px"
                          fontWeight="bold"
                          color="gray.800"
                        >
                          Default Template
                        </Text>
                      </Flex>
                      <Box
                        px={1.5}
                        py={0.2}
                        borderRadius="xs"
                        bg="teal.50"
                        color="teal.700"
                        fontSize="8px"
                        fontWeight="bold"
                      >
                        DEFAULT
                      </Box>
                    </Flex>
                    <Text fontSize="8.5px" color="gray.500" lineClamp={1}>
                      Standard notification layout & styling
                    </Text>
                  </Box>

                  {savedTemplates.length > 0 && (
                    <Box h="1px" bg="gray.200" my={0.5} />
                  )}

                  {savedTemplates.map((t) => (
                    <Box
                      key={t.id}
                      as="button"
                      onClick={() => onApplyTemplate(t)}
                      p={1.5}
                      borderRadius="md"
                      borderWidth="1px"
                      borderColor="gray.200"
                      bg="white"
                      textAlign="left"
                      cursor="pointer"
                      _hover={{
                        bg: "purple.50/50",
                        borderColor: "purple.300",
                      }}
                      transition="all 0.15s"
                    >
                      <Flex align="center" gap={1.5} mb={0.5}>
                        <Box
                          w="7px"
                          h="7px"
                          borderRadius="full"
                          bg={t.primary_color || "#0d9488"}
                        />
                        <Text
                          fontSize="10.5px"
                          fontWeight="bold"
                          color="gray.800"
                          lineClamp={1}
                        >
                          {t.name}
                        </Text>
                      </Flex>
                      {t.description && (
                        <Text fontSize="8.5px" color="gray.500" lineClamp={1}>
                          {t.description}
                        </Text>
                      )}
                    </Box>
                  ))}
                </VStack>
              </Box>
            )}
          </Box>

          {selectedTemplate && (
            <Flex
              align="center"
              gap={1}
              px={2}
              py="1px"
              h="18px"
              bg="purple.50"
              color="purple.700"
              borderWidth="1px"
              borderColor="purple.200"
              borderRadius="md"
              fontSize="9.5px"
              fontWeight="semibold"
            >
              <Box
                w="5px"
                h="5px"
                borderRadius="full"
                bg={selectedTemplate.primary_color || "#6e2fd5"}
              />
              <Text
                lineClamp={1}
                maxW="110px"
                title={selectedTemplate.name}
                fontSize="9.5px"
              >
                {selectedTemplate.name}
              </Text>
              <IconButton
                aria-label="Clear selected template"
                size="xs"
                variant="ghost"
                h="12px"
                w="12px"
                minW="12px"
                p={0}
                onClick={onResetTemplate}
                color="purple.600"
                _hover={{
                  color: "purple.900",
                  bg: "purple.100",
                }}
                title="Reset to default template"
              >
                <LuX size={9} />
              </IconButton>
            </Flex>
          )}
        </Flex>
      </Flex>
    </Flex>
  );
};
