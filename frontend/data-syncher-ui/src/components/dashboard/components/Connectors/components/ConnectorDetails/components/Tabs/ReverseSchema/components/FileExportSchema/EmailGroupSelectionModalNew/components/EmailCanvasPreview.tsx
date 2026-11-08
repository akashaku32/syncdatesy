import React, { Fragment } from "react";

import {
  Box,
  Button,
  Flex,
  IconButton,
  Input,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";

import { LuCheck, LuPencil, LuX } from "react-icons/lu";

import {
  type ActiveSection,
  type SectionStyleState,
  getDestinationTitle,
  getPathLabel,
  renderTemplateText,
} from "../helpers";

interface EmailCanvasPreviewProps {
  activeSection: ActiveSection;
  onSetActiveSection: (_section: ActiveSection) => void;
  headerSubtitle: string;
  headerBgColor: string;
  // Greeting
  greetingName: string;
  greetingStyles: SectionStyleState;
  isEditingGreeting: boolean;
  tempGreeting: string;
  onChangeTempGreeting: (_val: string) => void;
  onStartEditingGreeting: () => void;
  onSaveGreeting: () => void;
  onCancelGreeting: () => void;
  // Body Content
  bodyContent: string;
  bodyStyles: SectionStyleState;
  isEditingBody: boolean;
  tempBody: string;
  onChangeTempBody: (_val: string) => void;
  onStartEditingBody: () => void;
  onSaveBody: () => void;
  onCancelBody: () => void;
  // Metadata Fields
  selectedBodyFields: string[];
  destinationName?: string;
  pathLabel?: string | null;
  resolvedPath?: string;
  tableName: string;
  connectionName?: string;
  companyName?: string;
  sourceDisplayName?: string;
  columnsCount?: number;
  // Callout Box
  showCalloutBox: boolean;
  calloutBoxText: string;
  calloutBoxBgColor: string;
  calloutBoxBorderColor: string;
  calloutBoxTextColor: string;
  // CTA Button
  ctaButtonText: string;
  buttonAlign: "left" | "center" | "right";
  buttonVariant: "solid" | "outline" | "subtle";
  buttonBgColor: string;
  buttonTextColor: string;
  // Team Signoff
  teamName: string;
  teamStyles: SectionStyleState;
  isEditingTeam: boolean;
  tempTeam: string;
  onChangeTempTeam: (_val: string) => void;
  onStartEditingTeam: () => void;
  onSaveTeam: () => void;
  onCancelTeam: () => void;
}

export const EmailCanvasPreview: React.FC<EmailCanvasPreviewProps> = ({
  activeSection,
  onSetActiveSection,
  headerSubtitle,
  headerBgColor,
  greetingName,
  greetingStyles,
  isEditingGreeting,
  tempGreeting,
  onChangeTempGreeting,
  onStartEditingGreeting,
  onSaveGreeting,
  onCancelGreeting,
  bodyContent,
  bodyStyles,
  isEditingBody,
  tempBody,
  onChangeTempBody,
  onStartEditingBody,
  onSaveBody,
  onCancelBody,
  selectedBodyFields,
  destinationName = "",
  pathLabel,
  resolvedPath,
  tableName,
  connectionName,
  companyName,
  sourceDisplayName,
  columnsCount,
  showCalloutBox,
  calloutBoxText,
  calloutBoxBgColor,
  calloutBoxBorderColor,
  calloutBoxTextColor,
  ctaButtonText,
  buttonAlign,
  buttonVariant,
  buttonBgColor,
  buttonTextColor,
  teamName,
  teamStyles,
  isEditingTeam,
  tempTeam,
  onChangeTempTeam,
  onStartEditingTeam,
  onSaveTeam,
  onCancelTeam,
}) => {
  return (
    <Box
      bg="white"
      borderWidth="1px"
      borderColor="gray.100"
      borderRadius="md"
      p={3}
      flex={1}
      overflowY="auto"
      fontFamily="system-ui"
      fontSize="11px"
      color="gray.800"
      lineHeight="normal"
      minH="250px"
      display="flex"
      flexDirection="column"
    >
      <VStack align="stretch" gap={1.5}>
        {/* Header Subtitle & Accent Color Bar */}
        {headerSubtitle ? (
          <Box mb={2}>
            <Text
              fontSize="10px"
              fontWeight="800"
              color="gray.500"
              letterSpacing="0.05em"
              mb={1}
            >
              {headerSubtitle}
            </Text>
            <Box h="3px" bg={headerBgColor} borderRadius="full" />
          </Box>
        ) : null}

        {/* Greeting recipient */}
        <Box
          p="4px"
          border={
            activeSection === "greeting"
              ? "1.5px solid"
              : "1px solid transparent"
          }
          borderColor={
            activeSection === "greeting" ? "brand.500" : "transparent"
          }
          bg={activeSection === "greeting" ? "brand.50/30" : "transparent"}
          borderRadius="md"
          cursor="pointer"
          onClick={(e) => {
            e.stopPropagation();
            onSetActiveSection("greeting");
          }}
        >
          {isEditingGreeting ? (
            <Flex gap={1.5} align="center" onClick={(e) => e.stopPropagation()}>
              <Input
                size="xs"
                w="200px"
                value={tempGreeting}
                onChange={(e) => onChangeTempGreeting(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onSaveGreeting();
                  if (e.key === "Escape") onCancelGreeting();
                }}
                placeholder="Enter Greeting..."
                autoFocus
                bg="white"
                color={greetingStyles.color}
                fontFamily={greetingStyles.fontFamily}
                fontSize={greetingStyles.fontSize}
                fontWeight={greetingStyles.bold ? "bold" : "normal"}
                fontStyle={greetingStyles.italic ? "italic" : "normal"}
                borderColor="gray.300"
                h="22px"
              />
              <IconButton
                aria-label="Save greeting"
                size="xs"
                variant="ghost"
                colorPalette="green"
                onClick={onSaveGreeting}
                h="20px"
                w="20px"
                minW="20px"
              >
                <LuCheck size={11} />
              </IconButton>
              <IconButton
                aria-label="Cancel greeting"
                size="xs"
                variant="ghost"
                colorPalette="red"
                onClick={onCancelGreeting}
                h="20px"
                w="20px"
                minW="20px"
              >
                <LuX size={11} />
              </IconButton>
            </Flex>
          ) : (
            <Text
              fontFamily={greetingStyles.fontFamily}
              color={greetingStyles.color}
              fontSize={greetingStyles.fontSize}
              fontWeight={greetingStyles.bold ? "bold" : "normal"}
              fontStyle={greetingStyles.italic ? "italic" : "normal"}
            >
              <Box
                as="span"
                border="1px dashed"
                borderColor="brand.300"
                cursor="pointer"
                px={2}
                py={0.5}
                borderRadius="md"
                bg="brand.50/20"
                display="inline-flex"
                alignItems="center"
                gap={1}
                transition="all 0.2s"
                _hover={{
                  borderColor: "brand.500",
                  color: "brand.600",
                  bg: "brand.50/60",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  onStartEditingGreeting();
                }}
                title="Click to edit greeting"
              >
                {greetingName || (
                  <span
                    style={{
                      color: "#64748b",
                      fontStyle: "italic",
                      fontWeight: "normal",
                    }}
                  >
                    Enter Greeting...
                  </span>
                )}
                <LuPencil size={9} />
              </Box>
            </Text>
          )}
        </Box>

        {/* Custom body message content */}
        <Box
          p="4px"
          border={
            activeSection === "body" ? "1.5px solid" : "1px solid transparent"
          }
          borderColor={activeSection === "body" ? "brand.500" : "transparent"}
          bg={activeSection === "body" ? "brand.50/30" : "transparent"}
          borderRadius="md"
          cursor="pointer"
          onClick={(e) => {
            e.stopPropagation();
            onSetActiveSection("body");
          }}
        >
          {isEditingBody ? (
            <Box
              bg="gray.50"
              p={2.5}
              borderRadius="md"
              borderWidth="1px"
              borderColor="brand.100"
              onClick={(e) => e.stopPropagation()}
            >
              <Textarea
                size="xs"
                value={tempBody}
                onChange={(e) => onChangeTempBody(e.target.value)}
                rows={4}
                bg="white"
                color={bodyStyles.color}
                fontFamily={bodyStyles.fontFamily}
                fontSize={bodyStyles.fontSize}
                fontWeight={bodyStyles.bold ? "bold" : "normal"}
                fontStyle={bodyStyles.italic ? "italic" : "normal"}
                borderColor="gray.300"
                _focus={{ borderColor: "brand.500" }}
                autoFocus
                mb={2}
              />
              <Flex gap={2} align="center" justify="space-between" mb={2}>
                <Text fontSize="8px" color="gray.500">
                  Tags: {"{table}"}, {"{destination}"}, etc.
                </Text>
                <Flex gap={1}>
                  <Button
                    size="xs"
                    colorPalette="green"
                    onClick={onSaveBody}
                    h="22px"
                    px={2.5}
                    fontSize="10px"
                  >
                    Save
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    colorPalette="gray"
                    onClick={onCancelBody}
                    h="22px"
                    px={2.5}
                    fontSize="10px"
                  >
                    Cancel
                  </Button>
                </Flex>
              </Flex>
              {/* Clickable badges inside editor context */}
              <Flex wrap="wrap" gap={1}>
                {[
                  "{table}",
                  "{destination}",
                  "{connection}",
                  "{service}",
                  "{status}",
                  "{rows}",
                  "{columns}",
                  "{file}",
                  "{path}",
                  "{timestamp}",
                ].map((tag) => (
                  <Box
                    key={tag}
                    as="button"
                    onClick={() => onChangeTempBody(tempBody + tag)}
                    fontWeight="semibold"
                    color="brand.600"
                    px={1.5}
                    py={0.5}
                    bg="brand.50"
                    borderRadius="sm"
                    fontSize="8.5px"
                    cursor="pointer"
                    _hover={{ bg: "brand.100" }}
                  >
                    {tag}
                  </Box>
                ))}
              </Flex>
            </Box>
          ) : (
            <Box
              cursor="pointer"
              border="1px dashed"
              borderColor="brand.300"
              borderRadius="md"
              p={2.5}
              bg="brand.50/10"
              transition="all 0.2s"
              _hover={{
                borderColor: "brand.500",
                bg: "brand.50/30",
              }}
              onClick={(e) => {
                e.stopPropagation();
                onStartEditingBody();
              }}
              position="relative"
              title="Click to edit email body message template"
            >
              <Text
                fontFamily={bodyStyles.fontFamily}
                color={bodyStyles.color}
                fontSize={bodyStyles.fontSize}
                fontWeight={bodyStyles.bold ? "bold" : "normal"}
                fontStyle={bodyStyles.italic ? "italic" : "normal"}
                whiteSpace="pre-wrap"
              >
                {renderTemplateText(
                  bodyContent,
                  tableName,
                  destinationName,
                  resolvedPath,
                  connectionName,
                  companyName,
                  sourceDisplayName,
                  columnsCount,
                ) || (
                  <span
                    style={{
                      color: "#64748b",
                      fontStyle: "italic",
                      fontWeight: "normal",
                    }}
                  >
                    Enter Message Body...
                  </span>
                )}
              </Text>
              <Flex
                position="absolute"
                right={2}
                top={2}
                color="brand.500"
                _hover={{ color: "brand.700" }}
              >
                <LuPencil size={11} />
              </Flex>
            </Box>
          )}
        </Box>

        {/* Metadata fields table */}
        {selectedBodyFields.length > 0 ? (
          <Box mt={1} p={2.5} bg="gray.50" borderRadius="md">
            <Box
              display="grid"
              gridTemplateColumns="max-content 1fr"
              columnGap="40px"
              rowGap="4px"
            >
              {selectedBodyFields.map((fieldId) => {
                let label = "";
                let valueElement: React.ReactNode = null;

                switch (fieldId) {
                  case "service":
                    label = "Service";
                    valueElement = sourceDisplayName
                      ? `${sourceDisplayName} to ${getDestinationTitle(destinationName)}`
                      : getDestinationTitle(destinationName);
                    break;
                  case "connection":
                    label = "Connection";
                    valueElement = connectionName || "-";
                    break;
                  case "company":
                    label = "Company";
                    valueElement = companyName || "-";
                    break;
                  case "table":
                    label = "Table";
                    valueElement = tableName || "-";
                    break;
                  case "file":
                  case "file_name":
                    label = "File";
                    {
                      const fullPathStr =
                        resolvedPath || (tableName ? `${tableName}.xlsx` : "-");
                      valueElement =
                        fullPathStr.split("/").pop() ||
                        (tableName ? `${tableName}.xlsx` : "-");
                    }
                    break;
                  case "rows":
                    label = "Rows Exported";
                    valueElement = "1,250 (Example)";
                    break;
                  case "columns":
                    label = "Columns Exported";
                    valueElement =
                      columnsCount !== undefined ? String(columnsCount) : "7";
                    break;
                  case "path":
                    label = getPathLabel(destinationName, pathLabel);
                    valueElement = (
                      <Text
                        as="span"
                        color="brand.600"
                        textDecoration="underline"
                        cursor="pointer"
                        _hover={{ color: "brand.700" }}
                        fontFamily={bodyStyles.fontFamily}
                        fontSize={bodyStyles.fontSize}
                        fontStyle={bodyStyles.italic ? "italic" : "normal"}
                      >
                        {resolvedPath}
                      </Text>
                    );
                    break;
                  case "timestamp":
                    label = "Completed At";
                    valueElement = "2026-05-29 07:35:47 UTC";
                    break;
                }

                if (!label) return null;

                return (
                  <Fragment key={fieldId}>
                    <Text
                      fontFamily={bodyStyles.fontFamily}
                      fontSize={bodyStyles.fontSize}
                      color={bodyStyles.color}
                      fontWeight={550}
                      fontStyle={bodyStyles.italic ? "italic" : "normal"}
                    >
                      {label}
                    </Text>
                    <Text
                      fontFamily={bodyStyles.fontFamily}
                      fontSize={bodyStyles.fontSize}
                      color={bodyStyles.color}
                      fontWeight={bodyStyles.bold ? "bold" : "normal"}
                      fontStyle={bodyStyles.italic ? "italic" : "normal"}
                    >
                      {valueElement}
                    </Text>
                  </Fragment>
                );
              })}
            </Box>
          </Box>
        ) : (
          <Box
            mt={1}
            p={3}
            bg="gray.50"
            borderRadius="md"
            borderWidth="1.5px"
            borderStyle="dashed"
            borderColor="gray.200"
            textAlign="center"
          >
            <Text fontSize="10px" color="gray.400" fontStyle="italic">
              Select fields from Metadata dropdown to display here
            </Text>
          </Box>
        )}

        {/* Callout / Highlight Box */}
        {showCalloutBox && (
          <Box
            my={2.5}
            p={2.5}
            borderRadius="md"
            bg={calloutBoxBgColor}
            border="1px solid"
            borderColor={calloutBoxBorderColor}
          >
            <Text
              fontSize="11px"
              fontWeight="500"
              color={calloutBoxTextColor}
              whiteSpace="pre-wrap"
            >
              {renderTemplateText(
                calloutBoxText,
                tableName,
                destinationName,
                resolvedPath,
                connectionName,
                companyName,
                sourceDisplayName,
                columnsCount,
              )}
            </Text>
          </Box>
        )}

        {/* Action Button (CTA) */}
        {ctaButtonText && (
          <Box my={3}>
            <Flex
              justify={
                buttonAlign === "left"
                  ? "flex-start"
                  : buttonAlign === "right"
                    ? "flex-end"
                    : "center"
              }
            >
              <Box
                bg={
                  buttonVariant === "outline"
                    ? "transparent"
                    : buttonVariant === "subtle"
                      ? "brand.50/70"
                      : buttonBgColor
                }
                color={
                  buttonVariant === "outline" || buttonVariant === "subtle"
                    ? buttonBgColor
                    : buttonTextColor
                }
                border={
                  buttonVariant === "outline"
                    ? `1.5px solid ${buttonBgColor}`
                    : buttonBgColor.toLowerCase() === "#ffffff" ||
                        buttonBgColor.toLowerCase() === "#fff" ||
                        buttonBgColor === "white"
                      ? "1px solid #cbd5e1"
                      : "none"
                }
                borderRadius="md"
                px={4}
                py={1.5}
                fontSize="11.5px"
                fontWeight="bold"
                boxShadow="xs"
              >
                {ctaButtonText}
              </Box>
            </Flex>
          </Box>
        )}

        {/* Sign-off */}
        <Box
          mt={2}
          p="4px"
          border={
            activeSection === "team" ? "1.5px solid" : "1px solid transparent"
          }
          borderColor={activeSection === "team" ? "brand.500" : "transparent"}
          bg={activeSection === "team" ? "brand.50/30" : "transparent"}
          borderRadius="md"
          cursor="pointer"
          onClick={(e) => {
            e.stopPropagation();
            onSetActiveSection("team");
          }}
        >
          {isEditingTeam ? (
            <Flex
              gap={1.5}
              align="flex-start"
              mt={1}
              onClick={(e) => e.stopPropagation()}
            >
              <Textarea
                size="xs"
                w="220px"
                value={tempTeam}
                onChange={(e) => onChangeTempTeam(e.target.value)}
                rows={3}
                autoFocus
                bg="white"
                color={teamStyles.color}
                fontFamily={teamStyles.fontFamily}
                fontSize={teamStyles.fontSize}
                fontWeight={teamStyles.bold ? "bold" : "normal"}
                fontStyle={teamStyles.italic ? "italic" : "normal"}
                borderColor="gray.300"
                _focus={{ borderColor: "brand.500" }}
              />
              <Flex direction="column" gap={1}>
                <IconButton
                  aria-label="Save team"
                  size="xs"
                  variant="ghost"
                  colorPalette="green"
                  onClick={onSaveTeam}
                  h="20px"
                  w="20px"
                  minW="20px"
                >
                  <LuCheck size={11} />
                </IconButton>
                <IconButton
                  aria-label="Cancel team"
                  size="xs"
                  variant="ghost"
                  colorPalette="red"
                  onClick={onCancelTeam}
                  h="20px"
                  w="20px"
                  minW="20px"
                >
                  <LuX size={11} />
                </IconButton>
              </Flex>
            </Flex>
          ) : (
            <Box
              as="span"
              border="1px dashed"
              borderColor="brand.300"
              cursor="pointer"
              px={2}
              py={0.5}
              borderRadius="md"
              bg="brand.50/20"
              fontWeight="semibold"
              display="inline-flex"
              alignItems="center"
              gap={1}
              mt={1.5}
              transition="all 0.2s"
              _hover={{
                borderColor: "brand.500",
                color: "brand.600",
                bg: "brand.50/60",
              }}
              onClick={(e) => {
                e.stopPropagation();
                onStartEditingTeam();
              }}
              title="Click to edit signature team name"
            >
              <Text
                fontFamily={teamStyles.fontFamily}
                color={teamStyles.color}
                fontSize={teamStyles.fontSize}
                fontWeight={teamStyles.bold ? "bold" : "normal"}
                fontStyle={teamStyles.italic ? "italic" : "normal"}
                whiteSpace="pre-wrap"
              >
                {teamName || (
                  <span
                    style={{
                      color: "#64748b",
                      fontStyle: "italic",
                      fontWeight: "normal",
                    }}
                  >
                    Enter Sign-off...
                  </span>
                )}
              </Text>
              <LuPencil size={9} />
            </Box>
          )}
        </Box>
      </VStack>
    </Box>
  );
};
