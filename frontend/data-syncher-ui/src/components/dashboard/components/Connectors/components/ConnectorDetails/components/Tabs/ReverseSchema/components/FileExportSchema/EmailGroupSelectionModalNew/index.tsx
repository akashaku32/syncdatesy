import React, { useEffect, useRef, useState } from "react";

import {
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  Portal,
  Text,
  VStack,
} from "@chakra-ui/react";

import { useNavigate } from "react-router";

import { toaster } from "@/components/ui/toaster";
import useFetchEmailTemplates from "@/queryOptions/emailTemplates/useFetchEmailTemplates";
import { type EmailTemplate } from "@/types/emailTemplates";

import { EmailCanvasPreview } from "./components/EmailCanvasPreview";
import { EmailRecipientSelector } from "./components/EmailRecipientSelector";
import { EmailSubjectRow } from "./components/EmailSubjectRow";
import { EmailToolbar } from "./components/EmailToolbar";
import {
  type ActiveSection,
  DEFAULT_BODY_CONTENT,
  DEFAULT_BODY_FIELDS,
  DEFAULT_SUBJECT_TEMPLATE,
  DEFAULT_TEAM_NAME,
  type EmailGroupSelectionModalProps,
  type SectionStyleState,
  buildPreviewPath,
} from "./helpers";

export type * from "./helpers";

export const EmailGroupSelectionModalNew: React.FC<
  EmailGroupSelectionModalProps
> = ({
  open,
  onClose,
  tableName,
  emailGroups,
  initialSelectedGroupIds,
  initialEmailTemplateId,
  initialEmailCustomFields,
  destinationName = "SharePoint",
  pathLabel,
  onSave,
  isSaving,
  rootFolder,
  targetFolder,
  outputFileName,
  fileFormat,
  connectionName,
  companyName,
  sourceDisplayName,
  columnsCount,
}) => {
  const navigate = useNavigate();

  const resolvedPath = buildPreviewPath({
    rootFolder,
    targetFolder,
    outputFileName,
    fileFormat,
    tableName,
  });

  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectedBodyFields, setSelectedBodyFields] =
    useState<string[]>(DEFAULT_BODY_FIELDS);

  const [greetingName, setGreetingName] = useState<string>("");
  const [bodyContent, setBodyContent] = useState<string>("");
  const [teamName, setTeamName] = useState<string>("");
  const [subjectTemplate, setSubjectTemplate] = useState<string>(
    DEFAULT_SUBJECT_TEMPLATE,
  );

  const [subjectStyles, setSubjectStyles] = useState<SectionStyleState>({
    bold: false,
    italic: false,
    color: "#000000",
    fontFamily: "system-ui",
    fontSize: "12px",
  });
  const [greetingStyles, setGreetingStyles] = useState<SectionStyleState>({
    bold: false,
    italic: false,
    color: "#000000",
    fontFamily: "system-ui",
    fontSize: "12px",
  });
  const [bodyStyles, setBodyStyles] = useState<SectionStyleState>({
    bold: false,
    italic: false,
    color: "#000000",
    fontFamily: "system-ui",
    fontSize: "12px",
  });
  const [teamStyles, setTeamStyles] = useState<SectionStyleState>({
    bold: false,
    italic: false,
    color: "#000000",
    fontFamily: "system-ui",
    fontSize: "12px",
  });

  const [activeSection, setActiveSection] = useState<ActiveSection>("body");

  const getActiveStyle = (): SectionStyleState => {
    switch (activeSection) {
      case "subject":
        return subjectStyles;
      case "greeting":
        return greetingStyles;
      case "body":
        return bodyStyles;
      case "team":
        return teamStyles;
      default:
        return bodyStyles;
    }
  };

  const updateActiveStyle = (updates: Partial<SectionStyleState>) => {
    switch (activeSection) {
      case "subject":
        setSubjectStyles((prev) => ({ ...prev, ...updates }));
        break;
      case "greeting":
        setGreetingStyles((prev) => ({ ...prev, ...updates }));
        break;
      case "body":
        setBodyStyles((prev) => ({ ...prev, ...updates }));
        break;
      case "team":
        setTeamStyles((prev) => ({ ...prev, ...updates }));
        break;
    }
  };

  // Inline editing active states
  const [isEditingSubject, setIsEditingSubject] = useState(false);
  const [isEditingGreeting, setIsEditingGreeting] = useState(false);
  const [isEditingBody, setIsEditingBody] = useState(false);
  const [isEditingTeam, setIsEditingTeam] = useState(false);

  // Temporary editing text states
  const [tempSubject, setTempSubject] = useState("");
  const [tempGreeting, setTempGreeting] = useState("");
  const [tempBody, setTempBody] = useState("");
  const [tempTeam, setTempTeam] = useState("");

  const { data: savedTemplates = [] } = useFetchEmailTemplates();
  const [isMetadataDropdownOpen, setIsMetadataDropdownOpen] = useState(false);
  const [isTemplatesDropdownOpen, setIsTemplatesDropdownOpen] = useState(false);
  const [isToDropdownOpen, setIsToDropdownOpen] = useState(false);

  const metadataDropdownRef = useRef<HTMLDivElement>(null);
  const templatesDropdownRef = useRef<HTMLDivElement>(null);
  const toDropdownRef = useRef<HTMLDivElement>(null);

  // Custom Template Styling States
  const [headerSubtitle, setHeaderSubtitle] = useState("");
  const [headerBgColor, setHeaderBgColor] = useState("#6e2fd5");
  const [ctaButtonText, setCtaButtonText] = useState("");
  const [buttonBgColor, setButtonBgColor] = useState("#ffffff");
  const [buttonTextColor, setButtonTextColor] = useState("#1e293b");
  const [buttonAlign, setButtonAlign] = useState<"left" | "center" | "right">(
    "center",
  );
  const [buttonVariant, setButtonVariant] = useState<
    "solid" | "outline" | "subtle"
  >("solid");

  // Callout Box State
  const [showCalloutBox, setShowCalloutBox] = useState(false);
  const [calloutBoxText, setCalloutBoxText] = useState(
    "The report contains the records that require your attention.",
  );
  const [calloutBoxBgColor, setCalloutBoxBgColor] = useState("#fffbe6");
  const [calloutBoxBorderColor, setCalloutBoxBorderColor] = useState("#ffe58f");
  const [calloutBoxTextColor, setCalloutBoxTextColor] = useState("#873800");

  const [selectedTemplateId, setSelectedTemplateId] = useState<
    number | string | null
  >(initialEmailTemplateId ?? null);

  const applyCustomTemplate = (t: EmailTemplate) => {
    setSelectedTemplateId(t.id);
    if (t.subject) {
      setSubjectTemplate(t.subject);
      setTempSubject(t.subject);
    }
    if (t.greeting_name) {
      setGreetingName(t.greeting_name);
      setTempGreeting(t.greeting_name);
    }
    const isCalloutActive =
      t.show_callout_box !== undefined && t.show_callout_box !== null
        ? Boolean(t.show_callout_box)
        : Boolean(
            (t.callout_content && t.callout_content.trim() !== "") ||
              (t.callout_box_text && t.callout_box_text.trim() !== ""),
          );
    setShowCalloutBox(isCalloutActive);
    setCalloutBoxText(
      t.callout_content ||
        t.callout_box_text ||
        "The report contains the records that require your attention.",
    );

    const bodyVal = t.body_content ?? "";
    setBodyContent(bodyVal);
    setTempBody(bodyVal);

    if (t.team_name) {
      setTeamName(t.team_name);
      setTempTeam(t.team_name);
    }
    if (t.header_subtitle) {
      setHeaderSubtitle(t.header_subtitle);
    } else {
      setHeaderSubtitle("");
    }
    if (t.header_bg_color || t.primary_color) {
      setHeaderBgColor(t.header_bg_color || t.primary_color || "#6e2fd5");
    }
    if (t.cta_button_text) {
      setCtaButtonText(t.cta_button_text);
    } else {
      setCtaButtonText("View File");
    }
    setButtonBgColor(t.button_bg_color || "#ffffff");
    setButtonTextColor(t.button_text_color || "#1e293b");
    if (t.button_align) {
      setButtonAlign(
        (t.button_align as "left" | "center" | "right") || "center",
      );
    }
    if (t.button_variant) {
      setButtonVariant(
        (t.button_variant as "solid" | "outline" | "subtle") || "solid",
      );
    }
    if (t.body_color) {
      setBodyStyles((prev) => ({ ...prev, color: t.body_color || "#334155" }));
    }
    if (t.body_fields && Array.isArray(t.body_fields)) {
      setSelectedBodyFields(t.body_fields);
    }
    const calloutBg =
      t.callout_styles?.background_color || t.callout_box_bg_color || "#fffbe6";
    const calloutBorder =
      t.callout_styles?.border_color || t.callout_box_border_color || "#ffe58f";
    const calloutTextColor =
      t.callout_styles?.color || t.callout_box_text_color || "#873800";

    setCalloutBoxBgColor(calloutBg);
    setCalloutBoxBorderColor(calloutBorder);
    setCalloutBoxTextColor(calloutTextColor);
    setIsEditingSubject(false);
    setIsEditingGreeting(false);
    setIsEditingBody(false);
    setIsEditingTeam(false);
    setIsTemplatesDropdownOpen(false);
  };

  const resetToDefaultTemplate = () => {
    setSelectedTemplateId(null);
    setSubjectTemplate(DEFAULT_SUBJECT_TEMPLATE);
    setTempSubject(DEFAULT_SUBJECT_TEMPLATE);
    setGreetingName("");
    setTempGreeting("");
    setBodyContent("");
    setTempBody("");
    setTeamName("");
    setTempTeam("");
    setHeaderSubtitle("");
    setHeaderBgColor("#6e2fd5");
    setCtaButtonText("");
    setButtonBgColor("#ffffff");
    setButtonTextColor("#1e293b");
    setButtonAlign("center");
    setButtonVariant("solid");
    setSelectedBodyFields([]);
    setShowCalloutBox(false);
    setBodyStyles({
      bold: false,
      italic: false,
      color: "#000000",
      fontFamily: "system-ui",
      fontSize: "12px",
    });
    setIsEditingSubject(false);
    setIsEditingGreeting(false);
    setIsEditingBody(false);
    setIsEditingTeam(false);
    setIsTemplatesDropdownOpen(false);
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        metadataDropdownRef.current &&
        !metadataDropdownRef.current.contains(event.target as Node)
      ) {
        setIsMetadataDropdownOpen(false);
      }
      if (
        toDropdownRef.current &&
        !toDropdownRef.current.contains(event.target as Node)
      ) {
        setIsToDropdownOpen(false);
      }
      if (
        templatesDropdownRef.current &&
        !templatesDropdownRef.current.contains(event.target as Node)
      ) {
        setIsTemplatesDropdownOpen(false);
      }
    };
    if (isMetadataDropdownOpen || isToDropdownOpen || isTemplatesDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isMetadataDropdownOpen, isToDropdownOpen, isTemplatesDropdownOpen]);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => {
        const validGroupIds = (initialSelectedGroupIds || []).filter((id) =>
          emailGroups.some((group) => group.id === id),
        );
        setSelectedIds(validGroupIds);
        setSelectedBodyFields(
          Array.isArray(initialEmailCustomFields?.body_fields)
            ? initialEmailCustomFields.body_fields
            : DEFAULT_BODY_FIELDS,
        );
        setSelectedTemplateId(initialEmailTemplateId ?? null);
        if (initialEmailTemplateId && savedTemplates.length > 0) {
          const matched = savedTemplates.find(
            (t) => String(t.id) === String(initialEmailTemplateId),
          );
          if (matched) {
            applyCustomTemplate(matched);

            if (initialEmailCustomFields?.team_name) {
              setTeamName(initialEmailCustomFields.team_name);
              setTempTeam(initialEmailCustomFields.team_name);
            }
            if (initialEmailCustomFields?.greeting_name) {
              setGreetingName(initialEmailCustomFields.greeting_name);
              setTempGreeting(initialEmailCustomFields.greeting_name);
            }
            if (
              initialEmailCustomFields?.subject !== undefined &&
              initialEmailCustomFields?.subject !== null
            ) {
              setSubjectTemplate(initialEmailCustomFields.subject);
              setTempSubject(initialEmailCustomFields.subject);
            }
            if (
              initialEmailCustomFields?.body_content !== undefined &&
              initialEmailCustomFields?.body_content !== null
            ) {
              setBodyContent(initialEmailCustomFields.body_content);
              setTempBody(initialEmailCustomFields.body_content);
            }
            if (Array.isArray(initialEmailCustomFields?.body_fields)) {
              setSelectedBodyFields(initialEmailCustomFields.body_fields);
            }
            setIsEditingSubject(false);
            setIsEditingGreeting(false);
            setIsEditingBody(false);
            setIsEditingTeam(false);
            return;
          }
        }
        const subj =
          initialEmailCustomFields?.subject !== undefined &&
          initialEmailCustomFields?.subject !== null
            ? initialEmailCustomFields.subject
            : DEFAULT_SUBJECT_TEMPLATE;
        const greet =
          initialEmailCustomFields?.greeting_name !== undefined &&
          initialEmailCustomFields?.greeting_name !== null
            ? initialEmailCustomFields.greeting_name
            : "";
        const body =
          initialEmailCustomFields?.body_content !== undefined &&
          initialEmailCustomFields?.body_content !== null
            ? initialEmailCustomFields.body_content
            : DEFAULT_BODY_CONTENT;
        const team =
          initialEmailCustomFields?.team_name !== undefined &&
          initialEmailCustomFields?.team_name !== null
            ? initialEmailCustomFields.team_name
            : DEFAULT_TEAM_NAME;

        setHeaderSubtitle("");
        setHeaderBgColor("#6e2fd5");
        setCtaButtonText("");
        setButtonBgColor("#ffffff");
        setButtonTextColor("#1e293b");
        setButtonAlign("center");
        setButtonVariant("solid");
        setShowCalloutBox(false);
        setCalloutBoxText(
          "The report contains the records that require your attention.",
        );
        setCalloutBoxBgColor("#fffbe6");
        setCalloutBoxBorderColor("#ffe58f");
        setCalloutBoxTextColor("#873800");

        setSubjectTemplate(subj);
        setGreetingName(greet);
        setBodyContent(body);
        setTeamName(team);

        setTempSubject(subj);
        setTempGreeting(greet);
        setTempBody(body);
        setTempTeam(team);

        const sStyles = initialEmailCustomFields?.subject_styles;
        const gStyles = initialEmailCustomFields?.greeting_styles;
        const bStyles =
          initialEmailCustomFields?.body_styles ||
          initialEmailCustomFields?.styles;
        const tStyles = initialEmailCustomFields?.team_styles;

        setSubjectStyles({
          bold: !!sStyles?.bold,
          italic: !!sStyles?.italic,
          color: sStyles?.color || "#000000",
          fontFamily: sStyles?.font_family || "system-ui",
          fontSize: sStyles?.font_size || "12px",
        });

        setGreetingStyles({
          bold: !!gStyles?.bold,
          italic: !!gStyles?.italic,
          color: gStyles?.color || "#000000",
          fontFamily: gStyles?.font_family || "system-ui",
          fontSize: gStyles?.font_size || "12px",
        });

        setBodyStyles({
          bold: !!bStyles?.bold,
          italic: !!bStyles?.italic,
          color: bStyles?.color || "#000000",
          fontFamily: bStyles?.font_family || "system-ui",
          fontSize: bStyles?.font_size || "12px",
        });

        setTeamStyles({
          bold: !!tStyles?.bold,
          italic: !!tStyles?.italic,
          color: tStyles?.color || "#000000",
          fontFamily: tStyles?.font_family || "system-ui",
          fontSize: tStyles?.font_size || "12px",
        });

        setActiveSection("body");
        setIsEditingSubject(false);
        setIsEditingGreeting(false);
        setIsEditingBody(false);
        setIsEditingTeam(false);
        setIsMetadataDropdownOpen(false);
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [
    open,
    initialSelectedGroupIds,
    initialEmailTemplateId,
    initialEmailCustomFields,
    emailGroups,
    savedTemplates,
  ]);

  const toggleGroup = (id: number) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  const toggleBodyField = (id: string) => {
    setSelectedBodyFields((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  // Inline action saves
  const saveSubjectInline = () => {
    setSubjectTemplate(tempSubject.trim());
    setIsEditingSubject(false);
  };
  const cancelSubjectInline = () => {
    setTempSubject(subjectTemplate);
    setIsEditingSubject(false);
  };

  const saveGreetingInline = () => {
    setGreetingName(tempGreeting.trim());
    setIsEditingGreeting(false);
  };
  const cancelGreetingInline = () => {
    setTempGreeting(greetingName);
    setIsEditingGreeting(false);
  };

  const saveBodyInline = () => {
    setBodyContent(tempBody.trim());
    setIsEditingBody(false);
  };
  const cancelBodyInline = () => {
    setTempBody(bodyContent);
    setIsEditingBody(false);
  };

  const saveTeamInline = () => {
    setTeamName(tempTeam.trim());
    setIsEditingTeam(false);
  };
  const cancelTeamInline = () => {
    setTempTeam(teamName);
    setIsEditingTeam(false);
  };

  const handleGoToEmailSettings = () => {
    onClose();
    navigate("/dashboard/account/communication-support?tab=notifications");
  };

  const handleSave = () => {
    if (selectedIds.length === 0) {
      toaster.error({
        title: "Email Group Required",
        description:
          "Please select at least one email group to receive notifications.",
      });
      return;
    }

    const finalSubject = isEditingSubject
      ? tempSubject.trim()
      : subjectTemplate;
    const finalGreeting = isEditingGreeting
      ? tempGreeting.trim()
      : greetingName;
    const finalBody = isEditingBody ? tempBody.trim() : bodyContent;
    const finalTeam = isEditingTeam ? tempTeam.trim() : teamName;

    onSave(
      selectedIds,
      {
        subject: finalSubject,
        subject_styles: {
          bold: subjectStyles.bold,
          italic: subjectStyles.italic,
          color: subjectStyles.color,
          font_family: subjectStyles.fontFamily,
          font_size: subjectStyles.fontSize,
        },
        body_fields: selectedBodyFields,
        greeting_name: finalGreeting,
        greeting_styles: {
          bold: greetingStyles.bold,
          italic: greetingStyles.italic,
          color: greetingStyles.color,
          font_family: greetingStyles.fontFamily,
          font_size: greetingStyles.fontSize,
        },
        body_content: finalBody,
        body_styles: {
          bold: bodyStyles.bold,
          italic: bodyStyles.italic,
          color: bodyStyles.color,
          font_family: bodyStyles.fontFamily,
          font_size: bodyStyles.fontSize,
        },
        team_name: finalTeam,
        team_styles: {
          bold: teamStyles.bold,
          italic: teamStyles.italic,
          color: teamStyles.color,
          font_family: teamStyles.fontFamily,
          font_size: teamStyles.fontSize,
        },
        styles: {
          bold: bodyStyles.bold,
          italic: bodyStyles.italic,
          color: bodyStyles.color,
          font_family: bodyStyles.fontFamily,
          font_size: bodyStyles.fontSize,
        },
      },
      selectedTemplateId,
    );
    onClose();
  };

  return (
    <Dialog.Root lazyMount open={open} size="lg">
      <Portal>
        <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
        <Dialog.Positioner>
          <Dialog.Content
            borderRadius="xl"
            boxShadow="2xl"
            bg="white"
            overflow="hidden"
            maxW="680px"
            width="95%"
          >
            <Dialog.Header
              bg="gray.50"
              borderBottomWidth="1px"
              borderColor="gray.150"
              px={4}
              py={2.5}
            >
              <Dialog.Title color="gray.800" fontWeight="bold" fontSize="md">
                Email notifications: {tableName}
              </Dialog.Title>
            </Dialog.Header>

            <Dialog.Body pt={1} pb={3} px={3.5} bg="white">
              <VStack align="stretch" gap={1}>
                {/* Dynamic Email Client Mock */}
                <Box
                  borderWidth="1px"
                  borderColor="gray.200"
                  borderRadius="xl"
                  bg="white"
                  p={2.5}
                  boxShadow="sm"
                  display="flex"
                  flexDirection="column"
                  gap={1}
                >
                  {/* Header: Sender details */}
                  <Flex
                    justify="space-between"
                    align="center"
                    borderBottomWidth="1px"
                    borderColor="gray.100"
                    pb={1}
                  >
                    <Flex align="center" gap={2}>
                      <Flex
                        w="28px"
                        h="28px"
                        borderRadius="full"
                        bg="brand.500"
                        color="white"
                        align="center"
                        justify="center"
                        fontWeight="bold"
                        fontSize="10px"
                      >
                        DS
                      </Flex>
                      <Flex align="baseline" gap={1.5}>
                        <Text
                          fontSize="11px"
                          fontWeight="bold"
                          color="gray.800"
                          lineHeight="none"
                        >
                          DataSyncher Alert System
                        </Text>
                        <Text
                          fontSize="9.5px"
                          color="gray.500"
                          lineHeight="none"
                        >
                          &lt;support1@datasyncher.com&gt;
                        </Text>
                      </Flex>
                    </Flex>
                  </Flex>

                  {/* To Field with custom tag selection dropdown */}
                  <EmailRecipientSelector
                    emailGroups={emailGroups}
                    selectedIds={selectedIds}
                    isOpen={isToDropdownOpen}
                    onToggleOpen={() => setIsToDropdownOpen((prev) => !prev)}
                    onClose={() => setIsToDropdownOpen(false)}
                    onToggleGroup={toggleGroup}
                    onGoToEmailSettings={handleGoToEmailSettings}
                    containerRef={toDropdownRef}
                  />

                  {/* Subject Line */}
                  <EmailSubjectRow
                    activeSection={activeSection}
                    onSetActiveSection={setActiveSection}
                    isEditing={isEditingSubject}
                    onStartEditing={() => {
                      setIsEditingSubject(true);
                      setTempSubject(subjectTemplate);
                      setActiveSection("subject");
                    }}
                    tempSubject={tempSubject}
                    onChangeTempSubject={setTempSubject}
                    onSave={saveSubjectInline}
                    onCancel={cancelSubjectInline}
                    subjectTemplate={subjectTemplate}
                    tableName={tableName}
                    destinationName={destinationName}
                    resolvedPath={resolvedPath}
                    connectionName={connectionName}
                    companyName={companyName}
                    sourceDisplayName={sourceDisplayName}
                  />

                  {/* Toolbar */}
                  <EmailToolbar
                    activeSection={activeSection}
                    activeStyle={getActiveStyle()}
                    onUpdateActiveStyle={updateActiveStyle}
                    destinationName={destinationName}
                    pathLabel={pathLabel}
                    selectedBodyFields={selectedBodyFields}
                    onToggleBodyField={toggleBodyField}
                    isMetadataOpen={isMetadataDropdownOpen}
                    onToggleMetadataOpen={() =>
                      setIsMetadataDropdownOpen((prev) => !prev)
                    }
                    metadataRef={metadataDropdownRef}
                    savedTemplates={savedTemplates}
                    selectedTemplateId={selectedTemplateId}
                    isTemplatesOpen={isTemplatesDropdownOpen}
                    onToggleTemplatesOpen={() =>
                      setIsTemplatesDropdownOpen((prev) => !prev)
                    }
                    templatesRef={templatesDropdownRef}
                    onApplyTemplate={applyCustomTemplate}
                    onResetTemplate={resetToDefaultTemplate}
                    onNavigateToTemplates={() => {
                      onClose();
                      navigate(
                        "/dashboard/account/communication-support?tab=email_templates",
                      );
                    }}
                  />

                  {/* Canvas Preview Area */}
                  <EmailCanvasPreview
                    activeSection={activeSection}
                    onSetActiveSection={setActiveSection}
                    headerSubtitle={headerSubtitle}
                    headerBgColor={headerBgColor}
                    greetingName={greetingName}
                    greetingStyles={greetingStyles}
                    isEditingGreeting={isEditingGreeting}
                    tempGreeting={tempGreeting}
                    onChangeTempGreeting={setTempGreeting}
                    onStartEditingGreeting={() => {
                      setIsEditingGreeting(true);
                      setTempGreeting(greetingName);
                      setActiveSection("greeting");
                    }}
                    onSaveGreeting={saveGreetingInline}
                    onCancelGreeting={cancelGreetingInline}
                    bodyContent={bodyContent}
                    bodyStyles={bodyStyles}
                    isEditingBody={isEditingBody}
                    tempBody={tempBody}
                    onChangeTempBody={setTempBody}
                    onStartEditingBody={() => {
                      setIsEditingBody(true);
                      setTempBody(bodyContent);
                      setActiveSection("body");
                    }}
                    onSaveBody={saveBodyInline}
                    onCancelBody={cancelBodyInline}
                    selectedBodyFields={selectedBodyFields}
                    destinationName={destinationName}
                    pathLabel={pathLabel}
                    resolvedPath={resolvedPath}
                    tableName={tableName}
                    connectionName={connectionName}
                    companyName={companyName}
                    sourceDisplayName={sourceDisplayName}
                    columnsCount={columnsCount}
                    showCalloutBox={showCalloutBox}
                    calloutBoxText={calloutBoxText}
                    calloutBoxBgColor={calloutBoxBgColor}
                    calloutBoxBorderColor={calloutBoxBorderColor}
                    calloutBoxTextColor={calloutBoxTextColor}
                    ctaButtonText={ctaButtonText}
                    buttonAlign={buttonAlign}
                    buttonVariant={buttonVariant}
                    buttonBgColor={buttonBgColor}
                    buttonTextColor={buttonTextColor}
                    teamName={teamName}
                    teamStyles={teamStyles}
                    isEditingTeam={isEditingTeam}
                    tempTeam={tempTeam}
                    onChangeTempTeam={setTempTeam}
                    onStartEditingTeam={() => {
                      setIsEditingTeam(true);
                      setTempTeam(teamName);
                      setActiveSection("team");
                    }}
                    onSaveTeam={saveTeamInline}
                    onCancelTeam={cancelTeamInline}
                  />
                </Box>
              </VStack>
            </Dialog.Body>

            <Dialog.Footer
              bg="gray.50"
              borderTopWidth="1px"
              borderColor="gray.100"
              px={4}
              py={2.5}
            >
              <Button
                variant="outline"
                onClick={onClose}
                mr={3}
                disabled={isSaving}
                size="sm"
              >
                Cancel
              </Button>
              <Button
                colorPalette="brand"
                onClick={handleSave}
                px={6}
                borderRadius="full"
                loading={isSaving}
                size="sm"
              >
                Save Settings
              </Button>
            </Dialog.Footer>

            <Dialog.CloseTrigger asChild>
              <CloseButton
                size="sm"
                onClick={onClose}
                position="absolute"
                right={4}
                top={4}
              />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default EmailGroupSelectionModalNew;
