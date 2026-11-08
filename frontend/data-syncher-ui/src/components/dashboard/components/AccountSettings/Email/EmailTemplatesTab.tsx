import { useEffect, useRef, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  Dialog,
  Field,
  Flex,
  HStack,
  IconButton,
  Input,
  Portal,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";

import { LuCheck, LuPencil, LuPlus, LuTrash2, LuX } from "react-icons/lu";

import ColorPicker from "@/components/shared/ColorPicker";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import useCreateEmailTemplate from "@/queryOptions/emailTemplates/useCreateEmailTemplate";
import useDeleteEmailTemplate from "@/queryOptions/emailTemplates/useDeleteEmailTemplate";
import useFetchEmailTemplates from "@/queryOptions/emailTemplates/useFetchEmailTemplates";
import useUpdateEmailTemplate from "@/queryOptions/emailTemplates/useUpdateEmailTemplate";
import Table, { type Column } from "@/shared/Table";
import { type EmailTemplate } from "@/types/emailTemplates";

import MetadataTagSelector from "./components/MetadataTagSelector";
import { EMAIL_METADATA_FIELDS } from "./helpers/emailMetadataConstants";

export const EmailTemplatesTab = () => {
  const { data: templates = [], isLoading } = useFetchEmailTemplates();
  const { mutate: createTemplate, isPending: isCreating } =
    useCreateEmailTemplate();
  const { mutate: updateTemplate, isPending: isUpdating } =
    useUpdateEmailTemplate();
  const { mutate: deleteTemplate } = useDeleteEmailTemplate();

  // Search filter & pagination
  const [searchTerm, setSearchTerm] = useState("");
  const [, setCurrentPage] = useState(1);

  // Dialog State
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<EmailTemplate | null>(
    null,
  );

  // Form & WYSIWYG Canvas State
  const [name, setName] = useState("");
  const [subject, setSubject] = useState(
    "The {destination} export for {table} finished successfully.",
  );

  const [headerSubtitle, setHeaderSubtitle] = useState("");
  const [headerBgColor, setHeaderBgColor] = useState("#6e2fd5");

  const [greetingName, setGreetingName] = useState("");
  const [bodyContent, setBodyContent] = useState("");

  const [selectedBodyFields, setSelectedBodyFields] = useState<string[]>([]);

  const [ctaButtonText, setCtaButtonText] = useState("View File");
  const [buttonBgColor, setButtonBgColor] = useState("#ffffff");
  const [buttonTextColor, setButtonTextColor] = useState("#1e293b");
  const [buttonAlign, setButtonAlign] = useState<"left" | "center" | "right">(
    "center",
  );
  const [buttonVariant, setButtonVariant] = useState<
    "solid" | "outline" | "subtle"
  >("solid");

  const [teamName, setTeamName] = useState("");

  // Popover & Callout Box State
  const [isAddFieldOpen, setIsAddFieldOpen] = useState(false);
  const [showCalloutBox, setShowCalloutBox] = useState(false);
  const [calloutBoxText, setCalloutBoxText] = useState("");
  const [calloutBoxBgColor, setCalloutBoxBgColor] = useState("#f4faf8");
  const [calloutBoxBorderColor, setCalloutBoxBorderColor] = useState("#b2e0d8");
  const [calloutBoxTextColor, setCalloutBoxTextColor] = useState("#1e293b");

  const addFieldRef = useRef<HTMLDivElement>(null);
  const subjectInputRef = useRef<HTMLInputElement>(null);
  const headerSubtitleInputRef = useRef<HTMLInputElement>(null);
  const bodyTextareaRef = useRef<HTMLTextAreaElement>(null);
  const calloutTextareaRef = useRef<HTMLTextAreaElement>(null);

  const [activeField, setActiveField] = useState<
    "subject" | "header" | "body" | "callout" | null
  >(null);

  const handleInsertTagAtCursor = (tag: string) => {
    const activeEl = document.activeElement;

    let targetField: "subject" | "header" | "body" | "callout" | null = null;
    let targetInput: HTMLInputElement | HTMLTextAreaElement | null = null;

    if (activeEl === subjectInputRef.current) {
      targetField = "subject";
      targetInput = subjectInputRef.current;
    } else if (activeEl === headerSubtitleInputRef.current) {
      targetField = "header";
      targetInput = headerSubtitleInputRef.current;
    } else if (activeEl === bodyTextareaRef.current) {
      targetField = "body";
      targetInput = bodyTextareaRef.current;
    } else if (activeEl === calloutTextareaRef.current) {
      targetField = "callout";
      targetInput = calloutTextareaRef.current;
    } else if (activeField) {
      targetField = activeField;
      if (activeField === "subject") targetInput = subjectInputRef.current;
      else if (activeField === "header")
        targetInput = headerSubtitleInputRef.current;
      else if (activeField === "body") targetInput = bodyTextareaRef.current;
      else if (activeField === "callout")
        targetInput = calloutTextareaRef.current;
    }

    if (!targetField || !targetInput) {
      toaster.info({
        title: "Place cursor first",
        description:
          "Click inside Subject Line, Header Subtitle, Message Body, or Callout Box to position your cursor.",
      });
      return;
    }

    const el = targetInput;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    const val = el.value;
    const newVal = val.substring(0, start) + tag + val.substring(end);

    if (targetField === "subject") setSubject(newVal);
    else if (targetField === "header") setHeaderSubtitle(newVal);
    else if (targetField === "body") setBodyContent(newVal);
    else if (targetField === "callout") setCalloutBoxText(newVal);

    setTimeout(() => {
      el.focus();
      const pos = start + tag.length;
      el.setSelectionRange(pos, pos);
    }, 0);
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        addFieldRef.current &&
        !addFieldRef.current.contains(event.target as Node)
      ) {
        setIsAddFieldOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  // Delete Confirm State
  const [deletingId, setDeletingId] = useState<number | string | null>(null);

  const handleOpenAddDialog = () => {
    setEditingTemplate(null);
    setName("");
    setSubject("The {destination} export for {table} finished successfully.");
    setHeaderSubtitle("");
    setHeaderBgColor("#6e2fd5");
    setGreetingName("");
    setBodyContent("");
    setSelectedBodyFields([]);
    setShowCalloutBox(false);
    setCalloutBoxText("");
    setCalloutBoxBgColor("#fffbe6");
    setCalloutBoxBorderColor("#ffe58f");
    setCalloutBoxTextColor("#873800");
    setCtaButtonText("View File");
    setButtonBgColor("#ffffff");
    setButtonTextColor("#1e293b");
    setButtonAlign("center");
    setButtonVariant("solid");
    setTeamName("");
    setActiveField(null);
    setIsFormOpen(true);
  };

  const handleOpenEditDialog = (t: EmailTemplate) => {
    setEditingTemplate(t);
    setName(t.name);
    setSubject(t.subject);
    setHeaderSubtitle(t.header_subtitle || "");
    setHeaderBgColor(t.header_bg_color || t.primary_color || "#6e2fd5");
    setGreetingName(t.greeting_name || "Hi Team,");
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

    setBodyContent(t.body_content ?? "");

    setSelectedBodyFields(t.body_fields || []);
    const calloutBg =
      t.callout_styles?.background_color || t.callout_box_bg_color || "#fffbe6";
    const calloutBorder =
      t.callout_styles?.border_color || t.callout_box_border_color || "#ffe58f";
    const calloutTextColor =
      t.callout_styles?.color || t.callout_box_text_color || "#873800";

    setCalloutBoxBgColor(calloutBg);
    setCalloutBoxBorderColor(calloutBorder);
    setCalloutBoxTextColor(calloutTextColor);
    setCtaButtonText(t.cta_button_text || "View File");
    setButtonBgColor(t.button_bg_color || "#ffffff");
    setButtonTextColor(t.button_text_color || "#1e293b");
    setButtonAlign((t.button_align as "left" | "center" | "right") || "center");
    setButtonVariant(
      (t.button_variant as "solid" | "outline" | "subtle") || "solid",
    );
    setTeamName(
      t.team_name || "Thanks & Regards,\nDataSyncher Automated System",
    );
    setActiveField(null);
    setIsFormOpen(true);
  };

  const handleSave = () => {
    if (!name.trim()) {
      toaster.error({
        title: "Name Required",
        description: "Please enter a template name.",
      });
      return;
    }
    if (!subject.trim()) {
      toaster.error({
        title: "Subject Required",
        description: "Please enter a subject template.",
      });
      return;
    }

    const payload = {
      name: name.trim(),
      subject: subject.trim(),
      header_subtitle: headerSubtitle.trim() || undefined,
      header_bg_color: headerBgColor,
      primary_color: headerBgColor,
      greeting_name: greetingName.trim() || undefined,
      body_content: bodyContent,
      body_fields: selectedBodyFields,
      cta_button_text: ctaButtonText.trim() || undefined,
      button_bg_color: buttonBgColor,
      button_text_color: buttonTextColor,
      button_align: buttonAlign,
      button_variant: buttonVariant,
      team_name: teamName.trim() || undefined,
      show_callout_box: showCalloutBox,
      callout_content: showCalloutBox
        ? calloutBoxText.trim() || undefined
        : undefined,
      callout_styles: showCalloutBox
        ? {
            background_color: calloutBoxBgColor,
            border_color: calloutBoxBorderColor,
            color: calloutBoxTextColor,
          }
        : undefined,
      callout_box_text: showCalloutBox
        ? calloutBoxText.trim() || undefined
        : undefined,
      callout_box_bg_color: showCalloutBox ? calloutBoxBgColor : undefined,
      callout_box_border_color: showCalloutBox
        ? calloutBoxBorderColor
        : undefined,
      callout_box_text_color: showCalloutBox ? calloutBoxTextColor : undefined,
    };

    if (editingTemplate) {
      updateTemplate(
        { id: editingTemplate.id, payload },
        {
          onSuccess: () => {
            toaster.success({
              title: "Template updated",
              description: `Template "${name}" updated successfully.`,
            });
            setIsFormOpen(false);
          },
          onError: (err: Error) => {
            toaster.error({
              title: "Update failed",
              description: err.message || "Failed to update template.",
            });
          },
        },
      );
    } else {
      createTemplate(payload, {
        onSuccess: () => {
          toaster.success({
            title: "Template created",
            description: `Template "${name}" created successfully.`,
          });
          setIsFormOpen(false);
        },
        onError: (err: Error) => {
          toaster.error({
            title: "Creation failed",
            description: err.message || "Failed to create template.",
          });
        },
      });
    }
  };

  const handleDelete = (id: number | string) => {
    deleteTemplate(id, {
      onSuccess: () => {
        toaster.success({ title: "Template deleted successfully" });
        setDeletingId(null);
      },
      onError: (err: Error) => {
        toaster.error({
          title: "Delete failed",
          description: err.message || "Failed to delete template.",
        });
        setDeletingId(null);
      },
    });
  };

  const toggleField = (fieldId: string) => {
    if (selectedBodyFields.includes(fieldId)) {
      setSelectedBodyFields((prev) => prev.filter((id) => id !== fieldId));
    } else {
      setSelectedBodyFields((prev) => [...prev, fieldId]);
    }
  };

  const filteredTemplates = templates.filter((t) => {
    const q = searchTerm.toLowerCase();
    return (
      t.name.toLowerCase().includes(q) ||
      (t.description && t.description.toLowerCase().includes(q)) ||
      t.subject.toLowerCase().includes(q)
    );
  });

  const columns: Column<EmailTemplate>[] = [
    {
      header: "Template Name",
      accessor: "name",
      width: "220px",
      render: (val, row) => (
        <HStack gap={2}>
          <Box
            w="12px"
            h="12px"
            borderRadius="full"
            bg={row.header_bg_color || row.button_bg_color || "#6e2fd5"}
            flexShrink={0}
          />
          <Text fontWeight="600" color="gray.800" fontSize="sm">
            {String(val)}
          </Text>
        </HStack>
      ),
    },
    {
      header: "Subject Line Template",
      accessor: "subject",
      render: (val) => (
        <Text fontSize="xs" color="gray.700" fontFamily="mono" lineClamp={1}>
          {String(val)}
        </Text>
      ),
    },
    {
      header: "Theme Color",
      accessor: "header_bg_color",
      width: "120px",
      render: (_val, row) => {
        const color = row.header_bg_color || row.button_bg_color || "#6e2fd5";
        return (
          <HStack gap={1.5}>
            <Box
              w="14px"
              h="14px"
              borderRadius="sm"
              bg={color}
              borderWidth="1px"
              borderColor="gray.200"
            />
            <Text fontSize="xs" fontFamily="mono" color="gray.600">
              {color}
            </Text>
          </HStack>
        );
      },
    },
    {
      header: "Actions",
      accessor: "id",
      width: "120px",
      render: (_val, row) => (
        <HStack gap={1}>
          <IconButton
            aria-label="Edit template"
            size="xs"
            variant="ghost"
            colorPalette="brand"
            onClick={() => handleOpenEditDialog(row)}
          >
            <LuPencil size={14} />
          </IconButton>
          {deletingId === row.id ? (
            <HStack gap={1}>
              <IconButton
                aria-label="Confirm delete"
                size="xs"
                variant="solid"
                colorPalette="red"
                onClick={() => handleDelete(row.id)}
              >
                <LuCheck size={12} />
              </IconButton>
              <IconButton
                aria-label="Cancel delete"
                size="xs"
                variant="ghost"
                onClick={() => setDeletingId(null)}
              >
                <LuX size={12} />
              </IconButton>
            </HStack>
          ) : (
            <IconButton
              aria-label="Delete template"
              size="xs"
              variant="ghost"
              colorPalette="red"
              onClick={() => setDeletingId(row.id)}
            >
              <LuTrash2 size={14} />
            </IconButton>
          )}
        </HStack>
      ),
    },
  ];

  return (
    <Box>
      <Flex justify="space-between" align="center" mb={4}>
        <Input
          placeholder="Search templates..."
          size="sm"
          maxW="260px"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
        <Button size="sm" colorPalette="brand" onClick={handleOpenAddDialog}>
          <LuPlus /> Create Custom Template
        </Button>
      </Flex>

      {isLoading ? (
        <LoadingSpinner />
      ) : (
        <Table
          columns={columns}
          data={filteredTemplates}
          pageSize={10}
          totalElements={filteredTemplates.length}
          updateCurrentPage={(page) => setCurrentPage(page)}
        />
      )}

      {/* Template Form Modal */}
      <Dialog.Root
        lazyMount
        open={isFormOpen}
        onOpenChange={(e) => setIsFormOpen(e.open)}
        size="lg"
      >
        <Portal>
          <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
          <Dialog.Positioner>
            <Dialog.Content
              borderRadius="xl"
              boxShadow="2xl"
              maxW="720px"
              width="95%"
              bg="white"
            >
              <Dialog.Header
                bg="gray.50"
                borderBottomWidth="1px"
                borderColor="gray.200"
                px={4}
                py={2}
              >
                <Dialog.Title fontSize="sm" fontWeight="bold" color="gray.800">
                  {editingTemplate
                    ? "Edit Email Template"
                    : "Create Custom Email Template"}
                </Dialog.Title>
              </Dialog.Header>

              <Dialog.Body p={3.5}>
                <VStack align="stretch" gap={2.5}>
                  {/* Template Name Meta Row */}
                  <Field.Root required w="100%">
                    <Field.Label fontSize="11px" fontWeight="bold">
                      Template Name <Field.RequiredIndicator />
                    </Field.Label>
                    <Input
                      size="xs"
                      placeholder="e.g. Daily SharePoint Report"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      h="26px"
                    />
                  </Field.Root>

                  {/* Subject Line Bar (Matching screenshot) */}
                  <Box
                    bg="gray.50"
                    borderWidth="1px"
                    borderColor="gray.200"
                    borderRadius="lg"
                    p={2}
                  >
                    <Flex align="center" gap={2}>
                      <Text
                        fontSize="10px"
                        fontWeight="800"
                        color="gray.500"
                        letterSpacing="0.05em"
                      >
                        SUBJECT
                      </Text>
                      <Input
                        ref={subjectInputRef}
                        size="xs"
                        variant="subtle"
                        bg="white"
                        borderColor="gray.200"
                        fontSize="11.5px"
                        fontFamily="mono"
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                        onFocus={() => setActiveField("subject")}
                        flex={1}
                        h="24px"
                      />
                    </Flex>
                  </Box>

                  {/* Single Centralized Variable Insertion Toolbar */}
                  <Box
                    bg="purple.50/50"
                    borderWidth="1px"
                    borderColor="purple.200"
                    borderRadius="lg"
                    px={2.5}
                    py={1.5}
                  >
                    <Flex
                      align="center"
                      justify="space-between"
                      gap={2}
                      flexWrap="wrap"
                    >
                      <Text
                        fontSize="10px"
                        fontWeight="bold"
                        color="purple.700"
                        whiteSpace="nowrap"
                      >
                        Insert Variable (
                        {activeField === "subject"
                          ? "Subject Line"
                          : activeField === "header"
                            ? "Header Subtitle"
                            : activeField === "callout"
                              ? "Callout Box"
                              : activeField === "body"
                                ? "Message Body"
                                : "Click field to place cursor"}
                        ):
                      </Text>
                      <MetadataTagSelector
                        onSelectTag={handleInsertTagAtCursor}
                      />
                    </Flex>
                  </Box>

                  {/* WYSIWYG Interactive Email Canvas Box */}
                  <Box
                    borderWidth="1px"
                    borderColor="gray.200"
                    borderRadius="xl"
                    p={3}
                    bg="white"
                    boxShadow="sm"
                    position="relative"
                  >
                    {/* Header Subtitle & Accent Color Bar */}
                    <Box mb={2.5} position="relative">
                      <Flex justify="space-between" align="center" mb={1.5}>
                        <Input
                          ref={headerSubtitleInputRef}
                          size="xs"
                          variant="flushed"
                          border="none"
                          outline="none"
                          fontWeight="800"
                          fontSize="11px"
                          letterSpacing="0.05em"
                          color="gray.800"
                          value={headerSubtitle}
                          onChange={(e) => setHeaderSubtitle(e.target.value)}
                          onFocus={() => setActiveField("header")}
                          placeholder="Enter Header Subtitle..."
                          h="20px"
                          w="auto"
                          minW="140px"
                        />

                        {/* Accent Line Color Swatch Picker */}
                        <ColorPicker
                          value={headerBgColor}
                          onChange={(hex) =>
                            setHeaderBgColor(
                              hex.startsWith("#") ? hex : `#${hex}`,
                            )
                          }
                          w="16px"
                          h="16px"
                          borderRadius="sm"
                        />
                      </Flex>

                      {/* Accent Horizontal Line */}
                      <Box h="3px" bg={headerBgColor} borderRadius="full" />
                    </Box>

                    {/* Editable Greeting */}
                    <Box mb={2}>
                      <Input
                        size="xs"
                        variant="flushed"
                        border="none"
                        outline="none"
                        fontWeight="600"
                        fontSize="12px"
                        color="gray.850"
                        value={greetingName}
                        onChange={(e) => setGreetingName(e.target.value)}
                        placeholder="Enter Greeting..."
                        h="20px"
                      />
                    </Box>

                    {/* Editable Message Body */}
                    <Box mb={3}>
                      <Textarea
                        ref={bodyTextareaRef}
                        size="xs"
                        variant="flushed"
                        border="none"
                        outline="none"
                        fontSize="11.5px"
                        color="gray.750"
                        lineHeight="relaxed"
                        value={bodyContent}
                        onChange={(e) => setBodyContent(e.target.value)}
                        onFocus={() => setActiveField("body")}
                        placeholder="Enter Message Body..."
                        rows={2}
                        resize="vertical"
                        p={0}
                      />
                    </Box>

                    {/* Metadata Checkbox Section Inside Canvas */}
                    <VStack align="stretch" gap={1.5} my={3} pl={1}>
                      {selectedBodyFields.map((fieldId) => {
                        const opt = EMAIL_METADATA_FIELDS.find(
                          (f) => f.id === fieldId,
                        );
                        if (!opt) return null;
                        return (
                          <Flex key={fieldId} align="center" gap={2}>
                            <Checkbox.Root
                              checked
                              size="sm"
                              cursor="pointer"
                              onCheckedChange={() => toggleField(fieldId)}
                            >
                              <Checkbox.HiddenInput />
                              <Checkbox.Control borderRadius="xs" />
                            </Checkbox.Root>
                            <Text
                              fontSize="11px"
                              fontWeight="600"
                              color="gray.700"
                            >
                              {opt.label}
                            </Text>
                          </Flex>
                        );
                      })}
                    </VStack>

                    {/* Popover / Menu for Adding Metadata Fields */}
                    <Box my={2.5} position="relative" ref={addFieldRef}>
                      {!isAddFieldOpen ? (
                        <Flex align="center" gap={4}>
                          <Button
                            size="xs"
                            variant="ghost"
                            colorPalette="brand"
                            fontWeight="bold"
                            fontSize="11px"
                            onClick={() => setIsAddFieldOpen(true)}
                          >
                            + Add field
                          </Button>
                          {!showCalloutBox && (
                            <Button
                              size="xs"
                              variant="ghost"
                              colorPalette="brand"
                              fontWeight="bold"
                              fontSize="11px"
                              onClick={() => setShowCalloutBox(true)}
                            >
                              + Add Callout Box
                            </Button>
                          )}
                        </Flex>
                      ) : (
                        <Box
                          position="absolute"
                          left={0}
                          top="22px"
                          zIndex={99}
                          bg="white"
                          p={2.5}
                          borderRadius="lg"
                          boxShadow="lg"
                          borderWidth="1px"
                          borderColor="gray.200"
                          width="210px"
                        >
                          <Text
                            fontSize="10px"
                            fontWeight="bold"
                            color="gray.600"
                            mb={1.5}
                          >
                            Select Metadata Fields
                          </Text>
                          <VStack
                            align="stretch"
                            gap={1}
                            maxH="180px"
                            overflowY="auto"
                          >
                            {EMAIL_METADATA_FIELDS.map((f) => {
                              const isChecked = selectedBodyFields.includes(
                                f.id,
                              );
                              return (
                                <Flex
                                  key={f.id}
                                  align="center"
                                  gap={2}
                                  cursor="pointer"
                                  p={1}
                                  borderRadius="md"
                                  _hover={{ bg: "gray.50" }}
                                  onClick={() => toggleField(f.id)}
                                >
                                  <Checkbox.Root
                                    checked={isChecked}
                                    size="sm"
                                    pointerEvents="none"
                                  >
                                    <Checkbox.HiddenInput />
                                    <Checkbox.Control borderRadius="xs" />
                                  </Checkbox.Root>
                                  <Text fontSize="11px" color="gray.800">
                                    {f.label}
                                  </Text>
                                </Flex>
                              );
                            })}
                          </VStack>
                          <Flex justify="flex-end" mt={2}>
                            <Button
                              size="xs"
                              variant="subtle"
                              onClick={() => setIsAddFieldOpen(false)}
                            >
                              Done
                            </Button>
                          </Flex>
                        </Box>
                      )}
                    </Box>

                    {/* Highlight / Callout Box */}
                    {showCalloutBox && (
                      <Box
                        my={2.5}
                        p={3}
                        borderRadius="lg"
                        bg={calloutBoxBgColor}
                        border="1.5px solid"
                        borderColor={calloutBoxBorderColor}
                        position="relative"
                      >
                        <Flex
                          justify="space-between"
                          align="flex-start"
                          gap={2}
                          mb={1.5}
                        >
                          <Box flex={1}>
                            <Textarea
                              ref={calloutTextareaRef}
                              size="xs"
                              variant="flushed"
                              bg="transparent"
                              border="none"
                              outline="none"
                              fontSize="11.5px"
                              fontWeight="500"
                              color={calloutBoxTextColor}
                              value={calloutBoxText}
                              onChange={(e) =>
                                setCalloutBoxText(e.target.value)
                              }
                              onFocus={() => setActiveField("callout")}
                              placeholder="Enter callout note text..."
                              rows={2}
                              resize="vertical"
                              p={0}
                            />
                          </Box>
                          <IconButton
                            aria-label="Remove callout box"
                            size="xs"
                            variant="ghost"
                            colorPalette="red"
                            h="20px"
                            w="20px"
                            minW="20px"
                            onClick={() => setShowCalloutBox(false)}
                            title="Remove callout box"
                          >
                            <LuTrash2 size={13} />
                          </IconButton>
                        </Flex>

                        <Flex
                          align="center"
                          justify="space-between"
                          flexWrap="wrap"
                          gap={2}
                          mt={2}
                          pt={2}
                          borderTopWidth="1px"
                          borderColor={`${calloutBoxBorderColor}80`}
                        >
                          {/* Warning / Notice Color Presets */}
                          <HStack gap={1.5}>
                            <Text
                              fontSize="9.5px"
                              fontWeight="bold"
                              color="gray.600"
                            >
                              Presets:
                            </Text>
                            {[
                              {
                                name: "Warning (Yellow)",
                                bg: "#fffbe6",
                                border: "#ffe58f",
                                text: "#873800",
                              },
                              {
                                name: "Info (Blue)",
                                bg: "#e6f7ff",
                                border: "#91d5ff",
                                text: "#003a8c",
                              },
                              {
                                name: "Success (Green)",
                                bg: "#f6ffed",
                                border: "#b7eb8f",
                                text: "#135200",
                              },
                              {
                                name: "Purple",
                                bg: "#f9f5ff",
                                border: "#d8b4fe",
                                text: "#581c87",
                              },
                            ].map((preset) => (
                              <Box
                                key={preset.name}
                                w="16px"
                                h="16px"
                                borderRadius="full"
                                bg={preset.bg}
                                border="1.5px solid"
                                borderColor={preset.border}
                                cursor="pointer"
                                title={preset.name}
                                onClick={() => {
                                  setCalloutBoxBgColor(preset.bg);
                                  setCalloutBoxBorderColor(preset.border);
                                  setCalloutBoxTextColor(preset.text);
                                }}
                                _hover={{ transform: "scale(1.15)" }}
                                transition="all 0.15s"
                              />
                            ))}
                          </HStack>

                          {/* Custom ColorPickers */}
                          <HStack gap={2.5}>
                            <HStack gap={1}>
                              <Text fontSize="9.5px" color="gray.600">
                                Fill:
                              </Text>
                              <ColorPicker
                                value={calloutBoxBgColor}
                                onChange={(hex) =>
                                  setCalloutBoxBgColor(
                                    hex.startsWith("#") ? hex : `#${hex}`,
                                  )
                                }
                                w="15px"
                                h="15px"
                                borderRadius="2px"
                              />
                            </HStack>
                            <HStack gap={1}>
                              <Text fontSize="9.5px" color="gray.600">
                                Border:
                              </Text>
                              <ColorPicker
                                value={calloutBoxBorderColor}
                                onChange={(hex) =>
                                  setCalloutBoxBorderColor(
                                    hex.startsWith("#") ? hex : `#${hex}`,
                                  )
                                }
                                w="15px"
                                h="15px"
                                borderRadius="2px"
                              />
                            </HStack>
                            <HStack gap={1}>
                              <Text fontSize="9.5px" color="gray.600">
                                Text:
                              </Text>
                              <ColorPicker
                                value={calloutBoxTextColor}
                                onChange={(hex) =>
                                  setCalloutBoxTextColor(
                                    hex.startsWith("#") ? hex : `#${hex}`,
                                  )
                                }
                                w="15px"
                                h="15px"
                                borderRadius="2px"
                              />
                            </HStack>
                          </HStack>
                        </Flex>
                      </Box>
                    )}

                    {/* Interactive Inline Editable CTA Button */}
                    <VStack
                      my={3.5}
                      align={
                        buttonAlign === "left"
                          ? "flex-start"
                          : buttonAlign === "right"
                            ? "flex-end"
                            : "center"
                      }
                      gap={1.5}
                      w="100%"
                    >
                      {/* Compact Mini-Toolbar Placed Directly Above Button */}
                      <HStack
                        gap={2}
                        bg="gray.50"
                        borderWidth="1px"
                        borderColor="gray.200"
                        borderRadius="md"
                        px={2}
                        py={0.5}
                        h="24px"
                        boxShadow="2xs"
                      >
                        <HStack gap={1} title="Button Fill Color">
                          <Text
                            fontSize="9px"
                            fontWeight="bold"
                            color="gray.500"
                          >
                            Fill:
                          </Text>
                          <ColorPicker
                            value={buttonBgColor}
                            onChange={(hex) =>
                              setButtonBgColor(
                                hex.startsWith("#") ? hex : `#${hex}`,
                              )
                            }
                            w="14px"
                            h="14px"
                            borderRadius="2px"
                          />
                        </HStack>

                        <HStack gap={1} title="Button Text Color">
                          <Text
                            fontSize="9px"
                            fontWeight="bold"
                            color="gray.500"
                          >
                            Text:
                          </Text>
                          <ColorPicker
                            value={buttonTextColor}
                            onChange={(hex) =>
                              setButtonTextColor(
                                hex.startsWith("#") ? hex : `#${hex}`,
                              )
                            }
                            w="14px"
                            h="14px"
                            borderRadius="2px"
                          />
                        </HStack>

                        <HStack gap={0.5} title="Button Alignment">
                          {(["left", "center", "right"] as const).map(
                            (align) => (
                              <Button
                                key={align}
                                size="xs"
                                h="16px"
                                w="16px"
                                minW="16px"
                                p={0}
                                fontSize="8.5px"
                                fontWeight="bold"
                                variant={
                                  buttonAlign === align ? "solid" : "subtle"
                                }
                                colorPalette={
                                  buttonAlign === align ? "purple" : "gray"
                                }
                                onClick={() => setButtonAlign(align)}
                                title={`Align ${
                                  align.charAt(0).toUpperCase() + align.slice(1)
                                }`}
                              >
                                {align.charAt(0).toUpperCase()}
                              </Button>
                            ),
                          )}
                        </HStack>

                        <HStack gap={0.5} title="Button Style">
                          {(["solid", "outline"] as const).map((variant) => (
                            <Button
                              key={variant}
                              size="xs"
                              h="16px"
                              px={1}
                              fontSize="8.5px"
                              variant={
                                buttonVariant === variant ? "solid" : "subtle"
                              }
                              colorPalette={
                                buttonVariant === variant ? "purple" : "gray"
                              }
                              onClick={() => setButtonVariant(variant)}
                              title={`Style ${
                                variant.charAt(0).toUpperCase() +
                                variant.slice(1)
                              }`}
                            >
                              {variant === "solid" ? "Solid" : "Outline"}
                            </Button>
                          ))}
                        </HStack>
                      </HStack>

                      {/* Inline Button with Direct Text Input - Perfectly Positioned */}
                      <Box
                        display="inline-flex"
                        alignItems="center"
                        justifyContent="center"
                        bg={
                          buttonVariant === "solid"
                            ? buttonBgColor
                            : "transparent"
                        }
                        color={
                          buttonVariant === "solid"
                            ? buttonTextColor
                            : buttonBgColor
                        }
                        borderWidth={buttonVariant === "outline" ? "1.5px" : 0}
                        borderColor={buttonBgColor}
                        borderRadius="md"
                        px={3.5}
                        py={1}
                        boxShadow="xs"
                        transition="all 0.2s"
                      >
                        <Input
                          size="xs"
                          variant="flushed"
                          border="none"
                          outline="none"
                          textAlign="center"
                          fontWeight="semibold"
                          fontSize="12px"
                          color="inherit"
                          bg="transparent"
                          value={ctaButtonText}
                          onChange={(e) => setCtaButtonText(e.target.value)}
                          placeholder="View File"
                          h="22px"
                          w={`${Math.max(
                            (ctaButtonText || "View File").length * 8.5 + 20,
                            85,
                          )}px`}
                          minW="75px"
                          maxW="260px"
                          px={1}
                          _placeholder={{ color: "inherit", opacity: 0.6 }}
                        />
                      </Box>
                    </VStack>

                    {/* Editable Signature / Team Name */}
                    <Box pt={2} borderTopWidth="1px" borderColor="gray.100">
                      <Textarea
                        size="xs"
                        variant="flushed"
                        border="none"
                        outline="none"
                        fontSize="11px"
                        color="gray.600"
                        value={teamName}
                        onChange={(e) => setTeamName(e.target.value)}
                        placeholder="Enter Sign-off..."
                        rows={2}
                        resize="none"
                        p={0}
                      />
                    </Box>
                  </Box>
                </VStack>
              </Dialog.Body>

              <Dialog.Footer
                bg="gray.50"
                borderTopWidth="1px"
                borderColor="gray.200"
                px={4}
                py={2}
              >
                <HStack gap={2} justify="flex-end" w="100%">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setIsFormOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    colorPalette="brand"
                    onClick={handleSave}
                    loading={isCreating || isUpdating}
                  >
                    {editingTemplate ? "Update Template" : "Save Template"}
                  </Button>
                </HStack>
              </Dialog.Footer>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    </Box>
  );
};

export default EmailTemplatesTab;
