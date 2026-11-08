import { useMemo, useRef, useState } from "react";

import {
  Box,
  Button,
  Field,
  Flex,
  Input,
  NativeSelect,
  Portal,
  Select,
  Stack,
  Text,
  Textarea,
  createListCollection,
} from "@chakra-ui/react";

import { LuFile, LuPaperclip, LuSend, LuX } from "react-icons/lu";

import { format } from "date-fns";
import { useSearchParams } from "react-router";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import { dateTimeFormat } from "@/constants/common";
import { VIEW_CONFIG } from "@/constants/view-config";
import useCreateSupportTicket from "@/queryOptions/support/useCreateSupportTicket";
import useFetchSupportTicketChoices from "@/queryOptions/support/useFetchSupportTicketChoices";
import useFetchSupportTicketDetail from "@/queryOptions/support/useFetchSupportTicketDetail";
import useFetchSupportTickets from "@/queryOptions/support/useFetchSupportTickets";
import useFetchTicketReplies from "@/queryOptions/support/useFetchTicketReplies";
import useSendTicketReply from "@/queryOptions/support/useSendTicketReply";
import useFetchUserById from "@/queryOptions/user/useFetchUserById";
import {
  type CreateSupportTicketPayload,
  type SupportEditableField,
} from "@/types/support";

import CloseTicketConfirmationDialog from "./CloseTicketConfirmationDialog";
import SupportTable from "./SupportTable";

const FIELD_LABELS: Record<string, string> = {
  connection: "Connection",
  source_type: "Source Type",
  connection_name: "Connection Name",
  subject: "Subject",
  description: "Description",
  category: "Category",
  issue_type: "Issue Type",
  attachment: "Attachment",
  attachments: "Attachments",
};

const formatFieldLabel = (name: string) =>
  FIELD_LABELS[name] ||
  name
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

type SupportFormValues = {
  attachments: File[];
  category: string;
  connection: string;
  connection_name: string;
  description: string;
  subject: string;
  issue_type: string;
  source_type: string;
};

const initialValues: SupportFormValues = {
  attachments: [],
  category: "",
  connection: "",
  connection_name: "",
  description: "",
  subject: "",
  issue_type: "",
  source_type: "",
};

const TicketUserName = ({ userId }: { userId: number }) => {
  const { data: user, isLoading } = useFetchUserById(userId);
  if (isLoading) return <Text as="span">Loading...</Text>;
  if (!user) return <Text as="span">Unknown User</Text>;
  return <Text as="span">{`${user.first_name} ${user.last_name}`}</Text>;
};

const Support = () => {
  const [searchParams, setSearchParams] = useSearchParams();

  const view =
    (searchParams.get("view") as "list" | "create" | "detail") || "list";
  const selectedTicketId = searchParams.get("ticket_id")
    ? Number(searchParams.get("ticket_id"))
    : null;

  const setView = (newView: "list" | "create" | "detail") => {
    setSearchParams((prev) => {
      const nextParams = new URLSearchParams(prev);
      if (newView === "list") {
        nextParams.delete("view");
        nextParams.delete("ticket_id");
      } else {
        nextParams.set("view", newView);
        if (newView !== "detail") {
          nextParams.delete("ticket_id");
        }
      }
      return nextParams;
    });
  };

  const { data: tickets = [], isLoading: isLoadingTickets } =
    useFetchSupportTickets(view === "list");
  const { data: choices, isLoading: isLoadingChoices } =
    useFetchSupportTicketChoices(view === "create");
  const createSupportTicket = useCreateSupportTicket();

  const { data: ticketDetail, isLoading: isLoadingDetail } =
    useFetchSupportTicketDetail(selectedTicketId ?? "", view === "detail");

  const { data: replies = [] } = useFetchTicketReplies(
    selectedTicketId ?? "",
    view === "detail",
  );
  const sendReply = useSendTicketReply(selectedTicketId ?? "");

  const [replyText, setReplyText] = useState("");
  const [replyAttachments, setReplyAttachments] = useState<File[]>([]);
  const [showAllFiles, setShowAllFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [values, setValues] = useState<SupportFormValues>(initialValues);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [showCloseDialog, setShowCloseDialog] = useState(false);
  const [ticketIdToClose, setTicketIdToClose] = useState<number | null>(null);

  // Adjust state during render if status changes to closed
  const [prevStatus, setPrevStatus] = useState<string | undefined>(undefined);
  if (ticketDetail?.status !== prevStatus) {
    setPrevStatus(ticketDetail?.status);
    if (ticketDetail?.status?.toLowerCase() === "closed") {
      setReplyText("");
      setReplyAttachments([]);
    }
  }

  const closedByName =
    ticketDetail?.closed_by_admin_name ||
    ticketDetail?.closed_by_user_name ||
    (typeof ticketDetail?.closed_by_admin === "object"
      ? ticketDetail?.closed_by_admin?.full_name
      : undefined) ||
    (typeof ticketDetail?.closed_by_user === "object"
      ? ticketDetail?.closed_by_user?.first_name
      : undefined);

  const fields = useMemo(() => {
    const apiFields = choices?.editable_fields ?? [];

    const order = [
      "category",
      "issue_type",
      "source_type",
      "connection_name",
      "subject",
      "description",
      "attachment",
      "attachments",
    ];

    const orderedFields = order
      .map((name) => {
        if (name === "issue_type" && !values.category) return null;
        const field = apiFields.find((f) => f.name === name);
        return field;
      })
      .filter(Boolean) as SupportEditableField[];

    const additionalFields = apiFields.filter(
      (f) => !order.includes(f.name) && f.name !== "status",
    );

    return [...orderedFields, ...additionalFields];
  }, [choices?.editable_fields, values.category]);

  const sortedConnections = useMemo(
    () =>
      [...(choices?.connections ?? [])].sort((a, b) =>
        a.connection_name.localeCompare(b.connection_name),
      ),
    [choices?.connections],
  );

  const filteredConnections = useMemo(() => {
    if (!values.source_type) return sortedConnections;
    return sortedConnections.filter(
      (connection) => connection.connector_type === values.source_type,
    );
  }, [sortedConnections, values.source_type]);

  const handleSourceTypeChange = (sourceType: string) => {
    setValues((prev) => {
      const selectedConnection = sortedConnections.find(
        (connection) => String(connection.connection_id) === prev.connection,
      );
      const shouldClearConnection =
        !!selectedConnection &&
        selectedConnection.connector_type !== sourceType;

      return {
        ...prev,
        source_type: sourceType,
        connection: shouldClearConnection ? "" : prev.connection,
        connection_name: shouldClearConnection ? "" : prev.connection_name,
      };
    });

    setErrors((prev) => ({
      ...prev,
      source_type: "",
      connection: "",
      connection_name: "",
    }));
  };

  const handleTextChange = (
    name: Exclude<keyof SupportFormValues, "attachments">,
    value: string,
  ) => {
    setValues((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: "" }));
  };

  const handleAddAttachments = (files: FileList | null) => {
    if (!files) return;
    const newFiles = Array.from(files);
    setValues((prev) => ({
      ...prev,
      attachments: [...prev.attachments, ...newFiles],
    }));
    setErrors((prev) => ({ ...prev, attachment: "" }));
  };

  const handleRemoveAttachment = (index: number) => {
    setValues((prev) => ({
      ...prev,
      attachments: prev.attachments.filter((_, i) => i !== index),
    }));
  };

  const isFieldRequired = (field: SupportEditableField) => {
    const selectedCategoryName = choices?.categories?.find(
      (c) => String(c.category_id) === values.category,
    )?.name;
    const isBilling = selectedCategoryName?.toLowerCase() === "billing";

    // These fields are always required in the UI
    const alwaysRequired = ["category", "subject", "description"];
    if (alwaysRequired.includes(field.name)) return true;

    // These fields are required unless it's a Billing ticket
    if (field.name === "source_type" || field.name === "connection_name") {
      return !isBilling;
    }

    // Issue type is required if category is selected (and thus field is visible)
    if (field.name === "issue_type" && values.category) {
      return true;
    }

    return field.required;
  };

  const validateForm = () => {
    const nextErrors: Record<string, string> = {};

    fields.forEach((field: SupportEditableField) => {
      const required = isFieldRequired(field);

      if (!required) {
        return;
      }

      const label = formatFieldLabel(field.name);

      if (
        field.type === "FileField" ||
        field.name === "attachment" ||
        field.name === "attachments"
      ) {
        if (values.attachments.length === 0) {
          nextErrors[field.name] = `${label} is required`;
        }
        return;
      }

      const value = values[field.name as keyof SupportFormValues];
      if (typeof value !== "string" || !value.trim()) {
        nextErrors[field.name] = `${label} is required`;
      }
    });

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleCreateTicket = async () => {
    if (!validateForm()) {
      return;
    }

    const payload: CreateSupportTicketPayload = {
      attachment: values.attachments[0] || null, // fallback for legacy
      attachments: values.attachments,
      category: values.category,
      connection: values.connection,
      connection_name: values.connection_name,
      description: values.description,
      subject: values.subject,
      issue_type: values.issue_type,
      source_type: values.source_type,
    };

    const response = await createSupportTicket.mutateAsync(payload);

    toaster.success({
      title: response.message ?? "Support ticket created successfully",
    });
    setValues(initialValues);
    setErrors({});
    setView("list");
  };

  const renderField = (field: SupportEditableField) => {
    const label = formatFieldLabel(field.name);
    const required = isFieldRequired(field);

    if (
      String(field.type).toLowerCase() === "filefield" ||
      field.name === "attachment" ||
      field.name === "attachments"
    ) {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <Input
              type="file"
              p={1}
              multiple={field.multiple || field.name === "attachments"}
              onChange={(event) => handleAddAttachments(event.target.files)}
            />
            {values.attachments.length > 0 && (
              <Stack gap={1} mt={2}>
                {values.attachments.map((file, index) => (
                  <Flex
                    key={index}
                    align="center"
                    gap={2}
                    bg="gray.50"
                    px={2}
                    py={1}
                    borderRadius="sm"
                  >
                    <LuFile size={14} color="gray.500" />
                    <Text fontSize="sm" color="gray.600" flex="1" truncate>
                      {file.name}
                    </Text>
                    <Button
                      size="xs"
                      variant="ghost"
                      p={0}
                      h="auto"
                      color="red.500"
                      onClick={() => handleRemoveAttachment(index)}
                    >
                      <LuX size={14} />
                    </Button>
                  </Flex>
                ))}
              </Stack>
            )}
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    const selectedCategoryName = choices?.categories?.find(
      (c) => String(c.category_id) === values.category,
    )?.name;

    if (
      (field.name === "source_type" || field.name === "connection_name") &&
      selectedCategoryName?.toLowerCase() === "billing"
    ) {
      return null;
    }

    if (field.name === "source_type") {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <NativeSelect.Root size="sm">
              <NativeSelect.Field
                value={values.source_type}
                onChange={(event) => handleSourceTypeChange(event.target.value)}
              >
                <option value="" disabled hidden>
                  Select source type
                </option>
                {(choices?.source_type ?? []).map((sourceType: string) => (
                  <option key={sourceType} value={sourceType}>
                    {sourceType}
                  </option>
                ))}
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.name === "category") {
      // ... same collection logic ...
      const categoryCollection = createListCollection({
        items: (choices?.categories || []).map((cat) => ({
          label: cat.name,
          value: String(cat.category_id),
          description: cat.description,
        })),
      });

      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <Select.Root
              collection={categoryCollection}
              value={[values.category]}
              onValueChange={({ value }) => {
                handleTextChange("category", value[0]);
                handleTextChange("issue_type", "");
              }}
              size="sm"
            >
              <Select.HiddenSelect />
              <Select.Control>
                <Select.Trigger>
                  <Select.ValueText placeholder="Select category" />
                </Select.Trigger>
                <Select.IndicatorGroup>
                  <Select.Indicator />
                </Select.IndicatorGroup>
              </Select.Control>
              <Portal>
                <Select.Positioner>
                  <Select.Content>
                    {categoryCollection.items.map((item) => (
                      <Select.Item item={item} key={item.value} py={2}>
                        <Box>
                          <Text fontSize="sm">{item.label}</Text>
                          <Text
                            fontSize="xs"
                            color="gray.500"
                            whiteSpace="normal"
                          >
                            {item.description}
                          </Text>
                        </Box>
                        <Select.ItemIndicator />
                      </Select.Item>
                    ))}
                  </Select.Content>
                </Select.Positioner>
              </Portal>
            </Select.Root>
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.name === "issue_type") {
      // ... collection logic ...
      const filteredIssueTypes = (choices?.issue_types || []).filter(
        (it) => !values.category || String(it.category) === values.category,
      );
      const issueTypeCollection = createListCollection({
        items: filteredIssueTypes.map((it) => ({
          label: it.name,
          value: String(it.issue_type_id),
          description: it.description,
        })),
      });

      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <Select.Root
              collection={issueTypeCollection}
              value={[values.issue_type]}
              onValueChange={({ value }) =>
                handleTextChange("issue_type", value[0])
              }
              size="sm"
              disabled={!values.category}
            >
              <Select.HiddenSelect />
              <Select.Control>
                <Select.Trigger>
                  <Select.ValueText placeholder="Select issue type" />
                </Select.Trigger>
                <Select.IndicatorGroup>
                  <Select.Indicator />
                </Select.IndicatorGroup>
              </Select.Control>
              <Portal>
                <Select.Positioner>
                  <Select.Content>
                    {issueTypeCollection.items.map((item) => (
                      <Select.Item item={item} key={item.value} py={2}>
                        <Box>
                          <Text fontSize="sm">{item.label}</Text>
                          {item.description && (
                            <Text
                              fontSize="xs"
                              color="gray.500"
                              whiteSpace="normal"
                            >
                              {item.description}
                            </Text>
                          )}
                        </Box>
                        <Select.ItemIndicator />
                      </Select.Item>
                    ))}
                  </Select.Content>
                </Select.Positioner>
              </Portal>
            </Select.Root>
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.name === "description") {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <Textarea
              value={values.description}
              onChange={(event) =>
                handleTextChange("description", event.target.value)
              }
              placeholder="Describe the issue"
              rows={10}
              resize="vertical"
            />
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.name === "connection_name") {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <NativeSelect.Root size="sm">
              <NativeSelect.Field
                value={values.connection_name}
                onChange={(event) => {
                  const connName = event.target.value;
                  const connId = sortedConnections.find(
                    (c) => c.connection_name === connName,
                  )?.connection_id;
                  setValues((prev) => ({
                    ...prev,
                    connection_name: connName,
                    connection: String(connId || ""),
                  }));
                }}
              >
                <option value="" disabled hidden>
                  Select connection
                </option>
                {filteredConnections.map((conn) => (
                  <option key={conn.connection_id} value={conn.connection_name}>
                    {conn.connection_name}
                  </option>
                ))}
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.type === "ChoiceField" || field.choices) {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <NativeSelect.Root size="sm">
              <NativeSelect.Field
                value={values[field.name as keyof SupportFormValues] as string}
                onChange={(event) =>
                  handleTextChange(
                    field.name as Exclude<
                      keyof SupportFormValues,
                      "attachments"
                    >,
                    event.target.value,
                  )
                }
              >
                <option value="">Select {label.toLowerCase()}</option>
                {((field.choices as (string | number)[]) ?? []).map(
                  (choice) => (
                    <option key={choice} value={choice}>
                      {choice}
                    </option>
                  ),
                )}
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    if (field.name === "subject") {
      return (
        <Box key={field.name}>
          <Field.Root required={required} invalid={!!errors[field.name]}>
            <Field.Label>{label}</Field.Label>
            <Input
              value={values[field.name as keyof SupportFormValues] as string}
              onChange={(event) => {
                handleTextChange("subject", event.target.value);
              }}
              placeholder={`Enter ${label.toLowerCase()}`}
            />
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        </Box>
      );
    }

    return (
      <Box key={field.name}>
        <Field.Root required={required} invalid={!!errors[field.name]}>
          <Field.Label>{label}</Field.Label>
          <Input
            value={values[field.name as keyof SupportFormValues] as string}
            onChange={(event) =>
              handleTextChange(
                field.name as Exclude<keyof SupportFormValues, "attachments">,
                event.target.value,
              )
            }
            placeholder={`Enter ${label.toLowerCase()}`}
          />
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      </Box>
    );
  };

  if (
    (view === "list" && isLoadingTickets) ||
    (view === "create" && isLoadingChoices) ||
    (view === "detail" && isLoadingDetail)
  ) {
    return <LoadingSpinner />;
  }

  if (view === "detail" && ticketDetail) {
    return (
      <Flex direction="column" gap={0} h="100%" overflow="hidden">
        <Box px={6} py={0}>
          <PageHeader
            breadcrumbs={[
              { label: "Support", onClick: () => setView("list") },
              { label: "Detail" },
            ]}
            title={
              <Flex align="center" gap={3}>
                <Text
                  as="span"
                  fontWeight="normal"
                  fontSize="lg"
                  color="gray.500"
                >
                  {ticketDetail.ticket_id}
                </Text>
                <Text
                  as="span"
                  fontSize="lg"
                  fontWeight="normal"
                  color="gray.500"
                >
                  {ticketDetail.subject}
                </Text>
                {(ticketDetail.source_type || ticketDetail.connection_name) && (
                  <Text
                    as="span"
                    fontSize="sm"
                    color="gray.500"
                    fontWeight="normal"
                  >
                    ({ticketDetail.source_type}
                    {ticketDetail.connection_name
                      ? ` - ${ticketDetail.connection_name}`
                      : ""}
                    )
                  </Text>
                )}
              </Flex>
            }
            titleBold={false}
            rightElement={
              <Flex align="center">
                {ticketDetail.status?.toLowerCase() === "closed" ? (
                  <Box
                    px={3}
                    py={1}
                    bg="#6e2fd5"
                    borderRadius="md"
                    fontSize="sm"
                    fontWeight="medium"
                    color="white"
                  >
                    Closed
                  </Box>
                ) : (
                  <NativeSelect.Root size="sm" w="110px" colorPalette="brand">
                    <NativeSelect.Field
                      value=""
                      onChange={(e) => {
                        if (e.target.value === "Closed") {
                          setTicketIdToClose(Number(ticketDetail.ticket_id));
                          setShowCloseDialog(true);
                        }
                      }}
                      bg="#6e2fd5"
                      color="white"
                      fontWeight="medium"
                      _hover={{ bg: "#5a25ae" }}
                      cursor="pointer"
                    >
                      <option value="" disabled hidden>
                        Actions
                      </option>
                      <option
                        value="Open"
                        disabled
                        style={{ color: "#a0a0a0" }}
                      >
                        Open ✓
                      </option>
                      <option value="Closed" style={{ color: "#2d3748" }}>
                        Close
                      </option>
                    </NativeSelect.Field>
                    <NativeSelect.Indicator color="white" />
                  </NativeSelect.Root>
                )}
              </Flex>
            }
          />
        </Box>
        <CloseTicketConfirmationDialog
          open={showCloseDialog}
          onClose={() => {
            setShowCloseDialog(false);
            setTicketIdToClose(null);
          }}
          ticketId={ticketIdToClose ?? 0}
        />
        <Box borderBottom="1px solid" borderColor="gray.100" mx={6} mb={2} />

        {/* Scrollable Container for Conversation and Reply Interface */}
        <Box
          flex="1"
          minH={0}
          overflowY="auto"
          pb={4}
          display="flex"
          flexDirection="column"
        >
          {/* Conversation Container - Shading Removed, Border Added */}
          <Box
            bg="white"
            p={0}
            mx={6}
            mb={2}
            border="1px solid"
            borderColor="gray.200"
            borderRadius="sm"
          >
            <Stack gap={0}>
              {/* Replies from API - Reversed (Newest First) */}
              {[...replies].reverse().map((reply) => (
                <Box
                  key={reply.id}
                  pt={0}
                  pb={2}
                  mb={0}
                  borderBottom="1px solid"
                  borderColor="gray.200"
                >
                  <Flex align="center" gap={2} mb={0} px={2} pt={1}>
                    <Text fontWeight="normal" fontSize="xs" color="gray.600">
                      {"<"}
                      {reply.is_support_team ? (
                        "Support Team"
                      ) : (
                        <TicketUserName userId={ticketDetail.created_by} />
                      )}
                      {">"}
                    </Text>
                    <Text fontSize="xs" color="gray.400">
                      {"<"}
                      {format(new Date(reply.created_at), dateTimeFormat)}
                      {">"}
                    </Text>
                  </Flex>
                  <Box px={2} py={1} bg="gray.50">
                    <Text
                      fontSize="sm"
                      color="gray.700"
                      mb={2}
                      whiteSpace="pre-wrap"
                    >
                      {reply.message}
                    </Text>

                    {(() => {
                      const allAttachments = [...(reply.attachments || [])];
                      if (
                        reply.attachment &&
                        !allAttachments.some(
                          (a) =>
                            JSON.stringify(a) ===
                            JSON.stringify(reply.attachment),
                        )
                      ) {
                        allAttachments.push(reply.attachment);
                      }

                      if (allAttachments.length === 0) return null;

                      const getFileUrl = (raw: unknown): string => {
                        if (!raw) return "";
                        if (typeof raw === "string") return raw;
                        if (typeof raw === "object" && raw !== null) {
                          const obj = raw as Record<string, unknown>;
                          return String(
                            obj.file ||
                              obj.url ||
                              obj.attachment ||
                              obj.name ||
                              obj.path ||
                              JSON.stringify(raw),
                          );
                        }
                        return String(raw);
                      };

                      const getFileName = (url: string) => {
                        if (url.startsWith("{") || url.startsWith("["))
                          return "Attachment";
                        return url.split("/").pop() || url || "Unknown File";
                      };

                      return (
                        <Box
                          mt={1}
                          border="1px solid"
                          borderColor="gray.200"
                          borderRadius="sm"
                          bg="gray.50"
                          px={2}
                          py={1}
                          display="block"
                          w="full"
                          maxW="full"
                          maxH="200px"
                          overflowY="auto"
                          overflowX="hidden"
                        >
                          <Box
                            display="grid"
                            gridTemplateColumns={
                              allAttachments.length > 1
                                ? "max-content max-content"
                                : "1fr"
                            }
                            gapX={12}
                            gapY={1}
                            alignItems="center"
                          >
                            {allAttachments.map((rawAttach, idx) => {
                              const url = getFileUrl(rawAttach);
                              const isJson =
                                url.startsWith("{") || url.startsWith("[");
                              const displayName = isJson
                                ? `Attachment ${idx + 1}`
                                : getFileName(url);

                              return (
                                <Text
                                  key={idx}
                                  display="block"
                                  fontSize="11px"
                                  color="blue.600"
                                  cursor={isJson ? "default" : "pointer"}
                                  textDecoration={isJson ? "none" : "underline"}
                                  _hover={isJson ? {} : { color: "blue.800" }}
                                  onClick={() => {
                                    if (!isJson)
                                      window.open(
                                        `https://qa.datasyncher.com${url}`,
                                        "_blank",
                                      );
                                  }}
                                  title={displayName}
                                >
                                  {displayName}
                                </Text>
                              );
                            })}
                          </Box>
                        </Box>
                      );
                    })()}
                  </Box>
                </Box>
              ))}

              {/* Sample Greeting */}
              <Box
                pb={2}
                pt={0}
                mb={0}
                borderBottom="1px solid"
                borderColor="gray.200"
              >
                <Flex align="center" gap={2} mb={0} px={2} pt={1}>
                  <Text fontWeight="normal" fontSize="xs" color="gray.600">
                    {"<"}Support Team{">"}
                  </Text>
                  <Text fontSize="xs" color="gray.400">
                    {"<"}
                    {format(new Date(ticketDetail.created_at), dateTimeFormat)}
                    {">"}
                  </Text>
                </Flex>
                <Box px={2} py={1} bg="gray.50">
                  <Text
                    fontSize="sm"
                    color="gray.700"
                    mb={2}
                    whiteSpace="pre-wrap"
                  >
                    Hello! We have received your request and our technical team
                    is currently investigating the issue. We will update you
                    here as soon as we have more information.
                  </Text>
                </Box>
              </Box>

              {/* User's Original Message */}
              <Box
                pt={0}
                pb={2}
                mb={0}
                borderBottom="1px solid"
                borderColor="gray.100"
              >
                <Flex align="center" gap={2} mb={0} px={2} pt={1}>
                  <Text fontWeight="normal" fontSize="xs" color="gray.600">
                    {"<"}
                    <TicketUserName userId={ticketDetail.created_by} />
                    {">"}
                  </Text>
                  <Text fontSize="xs" color="gray.400">
                    {"<"}
                    {format(new Date(ticketDetail.created_at), dateTimeFormat)}
                    {">"}
                  </Text>
                </Flex>
                <Box px={2} py={1} bg="gray.50">
                  <Text
                    fontSize="sm"
                    color="gray.700"
                    mb={2}
                    whiteSpace="pre-wrap"
                  >
                    {ticketDetail.description}
                  </Text>
                  {(() => {
                    const allAttachments = [
                      ...(ticketDetail.attachments || []),
                    ];
                    if (
                      ticketDetail.attachment &&
                      !allAttachments.includes(ticketDetail.attachment)
                    ) {
                      allAttachments.push(ticketDetail.attachment);
                    }

                    if (allAttachments.length === 0) return null;

                    const getFileUrl = (raw: unknown): string => {
                      if (!raw) return "";
                      if (typeof raw === "string") return raw;
                      if (typeof raw === "object" && raw !== null) {
                        const obj = raw as Record<string, unknown>;
                        return String(
                          obj.file ||
                            obj.url ||
                            obj.attachment ||
                            obj.name ||
                            obj.path ||
                            JSON.stringify(raw),
                        );
                      }
                      return String(raw);
                    };

                    const getFileName = (url: string) => {
                      if (url.startsWith("{") || url.startsWith("["))
                        return "Attachment";
                      return url.split("/").pop() || url || "Unknown File";
                    };

                    return (
                      <Box
                        mt={1}
                        border="1px solid"
                        borderColor="gray.200"
                        borderRadius="sm"
                        bg="gray.50"
                        px={2}
                        py={1}
                        display="block"
                        w="full"
                        maxW="full"
                        maxH="200px"
                        overflowY="auto"
                        overflowX="hidden"
                      >
                        <Box
                          display="grid"
                          gridTemplateColumns={
                            allAttachments.length > 1
                              ? "max-content max-content"
                              : "1fr"
                          }
                          gapX={12}
                          gapY={1}
                          alignItems="center"
                        >
                          {allAttachments.map((rawAttach, idx) => {
                            const url = getFileUrl(rawAttach);
                            const isJson =
                              url.startsWith("{") || url.startsWith("[");
                            const displayName = isJson
                              ? `Attachment ${idx + 1}`
                              : getFileName(url);

                            return (
                              <Text
                                key={idx}
                                display="block"
                                fontSize="11px"
                                color="blue.600"
                                cursor={isJson ? "default" : "pointer"}
                                textDecoration={isJson ? "none" : "underline"}
                                _hover={isJson ? {} : { color: "blue.800" }}
                                onClick={() => {
                                  if (!isJson)
                                    window.open(
                                      `https://qa.datasyncher.com${url}`,
                                      "_blank",
                                    );
                                }}
                                title={displayName}
                              >
                                {displayName}
                              </Text>
                            );
                          })}
                        </Box>
                      </Box>
                    );
                  })()}
                </Box>
              </Box>
            </Stack>
          </Box>

          {/* Reply Interface - Optimized for Space */}
          <Box px={6}>
            <Box
              bg="white"
              borderRadius="md"
              border="1px solid"
              borderColor="gray.300"
              overflow="hidden"
            >
              <Textarea
                placeholder={
                  ticketDetail.status?.toLowerCase() === "closed"
                    ? `Closed${closedByName ? ` by ${closedByName}` : ""} on ${format(
                        new Date(ticketDetail.closed_at!),
                        dateTimeFormat,
                      )}`
                    : "Write a reply..."
                }
                size="sm"
                variant="outline"
                rows={4}
                border="none"
                _focus={{ ring: 0 }}
                resize="none"
                value={replyText}
                color={
                  ticketDetail.status?.toLowerCase() === "closed"
                    ? "gray.500"
                    : "gray.800"
                }
                _placeholder={{
                  color:
                    ticketDetail.status?.toLowerCase() === "closed"
                      ? "gray.500"
                      : "gray.400",
                }}
                _disabled={{ color: "gray.500", opacity: 1 }}
                onChange={(e) => setReplyText(e.target.value)}
                disabled={ticketDetail.status?.toLowerCase() === "closed"}
                px={2}
                py={2}
              />
              {replyAttachments.length > 0 && (
                <Box px={2} pb={2}>
                  {(showAllFiles
                    ? replyAttachments
                    : replyAttachments.slice(0, 3)
                  ).map((file, i) => (
                    <Flex key={i} align="center" gap={2} py="2px">
                      <LuFile size={13} color="#718096" />
                      <Text fontSize="xs" color="gray.600" flex="1" truncate>
                        {file.name}
                      </Text>
                      <Button
                        size="xs"
                        variant="ghost"
                        p={0}
                        h="auto"
                        onClick={() =>
                          setReplyAttachments((prev) =>
                            prev.filter((_, idx) => idx !== i),
                          )
                        }
                      >
                        <LuX size={13} />
                      </Button>
                    </Flex>
                  ))}
                  {replyAttachments.length > 3 && (
                    <Button
                      size="xs"
                      variant="ghost"
                      color="#6e2fd5"
                      p={0}
                      mt={1}
                      onClick={() => setShowAllFiles((v) => !v)}
                    >
                      {showAllFiles
                        ? "Show less"
                        : `+${replyAttachments.length - 3} more files`}
                    </Button>
                  )}
                </Box>
              )}
              <Flex
                justify="space-between"
                align="center"
                px={2}
                py={2}
                bg="gray.50"
                borderTop="1px solid"
                borderColor="gray.100"
              >
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={ticketDetail.status?.toLowerCase() === "closed"}
                  color="gray.600"
                >
                  <LuPaperclip size={16} />
                  <Text ml={1}>Attach File</Text>
                </Button>
                <input
                  type="file"
                  ref={fileInputRef}
                  style={{ display: "none" }}
                  multiple
                  onChange={(e) => {
                    const files = Array.from(e.target.files || []);
                    setReplyAttachments((prev) => [...prev, ...files]);
                    e.target.value = "";
                  }}
                />
                <Button
                  size="sm"
                  colorPalette="brand"
                  px={6}
                  disabled={
                    ticketDetail.status?.toLowerCase() === "closed" ||
                    (!replyText.trim() && replyAttachments.length === 0)
                  }
                  loading={sendReply.isPending}
                  onClick={async () => {
                    const currentText = replyText;
                    const currentFiles = replyAttachments;
                    setReplyText("");
                    setReplyAttachments([]);
                    setShowAllFiles(false);
                    try {
                      await sendReply.mutateAsync({
                        message: currentText,
                        attachments: currentFiles,
                      });
                      toaster.success({
                        title: "Reply sent",
                        description: "Your reply has been successfully sent.",
                      });
                      setView("list");
                    } catch {
                      setReplyText(currentText);
                      setReplyAttachments(currentFiles);
                      toaster.error({
                        title: "Error",
                        description: "Failed to send reply. Please try again.",
                      });
                    }
                  }}
                >
                  <LuSend style={{ marginRight: "8px" }} />
                  Send Reply
                </Button>
              </Flex>
            </Box>
          </Box>
        </Box>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={[
          {
            label: "Support",
            onClick:
              view !== "list"
                ? () => {
                    setView("list");
                    setErrors({});
                  }
                : undefined,
          },
          ...(view === "create" ? [{ label: "Create Ticket" }] : []),
        ]}
        title={view === "list" ? "Tickets" : "Create Ticket"}
        buttonLabel={view === "list" ? "Create Ticket" : undefined}
        onCreateClick={view === "list" ? () => setView("create") : undefined}
      />

      {view === "list" ? (
        <SupportTable
          tickets={tickets}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onViewTicket={(id) => {
            setSearchParams((prev) => {
              const nextParams = new URLSearchParams(prev);
              nextParams.set("view", "detail");
              nextParams.set("ticket_id", String(id));
              return nextParams;
            });
          }}
        />
      ) : view === "create" ? (
        <Box maxW="3xl" p={{ base: 4, md: 0 }} mt={-5}>
          <Stack gap={2}>
            {fields.map((field: SupportEditableField) => renderField(field))}
          </Stack>

          <Flex justifyContent="flex-end" mt={6}>
            <Button
              colorPalette="brand"
              onClick={() => void handleCreateTicket()}
              loading={createSupportTicket.isPending}
              disabled={createSupportTicket.isPending}
            >
              Create Ticket
            </Button>
          </Flex>
        </Box>
      ) : null}
      <CloseTicketConfirmationDialog
        open={showCloseDialog}
        onClose={() => {
          setShowCloseDialog(false);
          setTicketIdToClose(null);
        }}
        ticketId={ticketIdToClose ?? 0}
      />
    </Flex>
  );
};
export default Support;
