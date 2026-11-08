import { useState } from "react";

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Dialog,
  Field,
  Flex,
  HStack,
  IconButton,
  Input,
  Portal,
  Switch,
  Text,
  Textarea,
} from "@chakra-ui/react";

import { LuPencil, LuPlus, LuTrash2 } from "react-icons/lu";

import { useSearchParams } from "react-router";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import { VIEW_CONFIG } from "@/constants/view-config";
import useFetchCommunicationSupportDetails from "@/queryOptions/communicationSupport/useFetchCommunicationSupportDetails";
import useUpdateCommunicationSupport from "@/queryOptions/communicationSupport/useUpdateCommunicationDetails";
import useCreateEmailGroup from "@/queryOptions/emailGroups/useCreateEmailGroup";
import useDeleteEmailGroup from "@/queryOptions/emailGroups/useDeleteEmailGroup";
import useFetchEmailGroups from "@/queryOptions/emailGroups/useFetchEmailGroups";
import useUpdateEmailGroup from "@/queryOptions/emailGroups/useUpdateEmailGroup";
import Table, { type Column } from "@/shared/Table";
import { type EmailGroup } from "@/types/emailGroups";

import { EmailTemplatesTab } from "./EmailTemplatesTab";

const EMAIL_SEPARATOR_HINT = "Enter emails separated by semicolons (;).";
const EMAIL_SEPARATOR_ERROR =
  "Emails must be separated by semicolons (;), not commas (,).";

const parseEmailList = (value: string) => {
  const normalizedValue = value.trim();

  if (normalizedValue.includes(",") && !normalizedValue.includes(";")) {
    return { error: EMAIL_SEPARATOR_ERROR, emails: [] as string[] };
  }

  const emails = normalizedValue
    .split(";")
    .map((email) => email.trim())
    .filter((email) => email.length > 0);

  if (emails.some((email) => email.includes(","))) {
    return { error: EMAIL_SEPARATOR_ERROR, emails: [] as string[] };
  }

  return { error: null as string | null, emails };
};

const DataLoadTab = () => {
  const { data, isLoading } = useFetchCommunicationSupportDetails();
  const { mutate: updateCommunicationSupport, isPending: isUpdating } =
    useUpdateCommunicationSupport();

  // local overrides become non-null only after user interaction
  const [localChecked, setLocalChecked] = useState<boolean | null>(null);
  const [localEmailAddresses, setLocalEmailAddresses] = useState<string | null>(
    null,
  );

  const checked = localChecked ?? data?.is_active ?? false;
  const emailAddresses =
    localEmailAddresses ?? data?.email_addresses?.join("; ") ?? "";

  if (isLoading) {
    return <LoadingSpinner />;
  }

  const handleSaveChanges = () => {
    // check if emails are valid (basic validation)
    const { error, emails: emailsArray } = parseEmailList(emailAddresses);
    if (error) {
      toaster.error({
        title: "Invalid email separator",
        description: error,
      });
      return;
    }

    const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    for (const email of emailsArray) {
      if (!emailRegex.test(email)) {
        toaster.error({
          title: `Invalid email address: ${email}`,
          description: EMAIL_SEPARATOR_HINT,
        });
        return;
      }
    }
    updateCommunicationSupport(
      {
        is_active: checked,
        // The API expects this field as a comma-delimited string.
        email_addresses_input: emailsArray.join(", "),
      },
      {
        onSuccess: (response) => {
          toaster.success({
            title: "Communication support updated",
            description: `Your communication support settings have been updated.`,
          });
          // reset local overrides
          setLocalChecked(response.is_active);
          setLocalEmailAddresses(response.email_addresses?.join("; ") ?? "");
        },
      },
    );
  };

  return (
    <Flex w="md" direction="column" gap={4} mt={2}>
      <Field.Root required>
        <Field.Label>
          Email addresses <Field.RequiredIndicator />
        </Field.Label>
        <Textarea
          placeholder="email@developer.com; email@designer.com"
          variant="outline"
          value={emailAddresses}
          onChange={(e) => setLocalEmailAddresses(e.target.value)}
        />
        <Field.HelperText>{EMAIL_SEPARATOR_HINT}</Field.HelperText>
      </Field.Root>
      <Switch.Root
        colorPalette="brand"
        checked={checked}
        onCheckedChange={(details) => setLocalChecked(!!details?.checked)}
      >
        <Switch.HiddenInput />
        <Switch.Control>
          <Switch.Thumb />
        </Switch.Control>
        <Switch.Label>Enable communication support</Switch.Label>
      </Switch.Root>
      <Button
        variant="solid"
        colorPalette="brand"
        ml="auto"
        onClick={handleSaveChanges}
        loading={isUpdating}
      >
        Save changes
      </Button>
    </Flex>
  );
};

const NotificationsTab = () => {
  const { data: emailGroups = [], isLoading: isGroupsLoading } =
    useFetchEmailGroups();
  const { mutate: createGroup, isPending: isCreating } = useCreateEmailGroup();
  const { mutate: updateGroup, isPending: isUpdating } = useUpdateEmailGroup();
  const { mutate: deleteGroup, isPending: isDeleting } = useDeleteEmailGroup();

  // Dialog State
  const [isFormDialogOpen, setIsFormDialogOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<EmailGroup | null>(null);
  const [groupName, setGroupName] = useState("");
  const [groupEmails, setGroupEmails] = useState("");

  // Delete State
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [deletingGroupId, setDeletingGroupId] = useState<
    number | string | null
  >(null);

  const handleOpenAddDialog = () => {
    setEditingGroup(null);
    setGroupName("");
    setGroupEmails("");
    setIsFormDialogOpen(true);
  };

  const handleOpenEditDialog = (group: EmailGroup) => {
    setEditingGroup(group);
    setGroupName(group.name);
    setGroupEmails(group.email_addresses.join("; "));
    setIsFormDialogOpen(true);
  };

  const handleOpenDeleteDialog = (id: number | string) => {
    setDeletingGroupId(id);
    setIsDeleteDialogOpen(true);
  };

  const handleSaveGroup = () => {
    if (!groupName.trim()) {
      toaster.error({
        title: "Group Name Required",
        description: "Please enter a group name.",
      });
      return;
    }

    const { error, emails: emailsArray } = parseEmailList(groupEmails);
    if (error) {
      toaster.error({
        title: "Invalid email separator",
        description: error,
      });
      return;
    }

    if (emailsArray.length === 0) {
      toaster.error({
        title: "Emails Required",
        description: "Please enter at least one email address.",
      });
      return;
    }

    const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
    for (const email of emailsArray) {
      if (!emailRegex.test(email)) {
        toaster.error({
          title: `Invalid email address: ${email}`,
          description: "Please enter valid email addresses.",
        });
        return;
      }
    }

    if (editingGroup) {
      updateGroup(
        {
          id: editingGroup.id,
          payload: {
            name: groupName.trim(),
            email_addresses: emailsArray,
          },
        },
        {
          onSuccess: () => {
            toaster.success({
              title: "Email group updated",
              description: `Group "${groupName}" has been successfully updated.`,
            });
            setIsFormDialogOpen(false);
          },
          onError: (err: Error) => {
            toaster.error({
              title: "Failed to update email group",
              description: err.message || "An error occurred.",
            });
          },
        },
      );
    } else {
      createGroup(
        {
          name: groupName.trim(),
          email_addresses: emailsArray,
        },
        {
          onSuccess: () => {
            toaster.success({
              title: "Email group created",
              description: `Group "${groupName}" has been successfully created.`,
            });
            setIsFormDialogOpen(false);
          },
          onError: (err: Error) => {
            toaster.error({
              title: "Failed to create email group",
              description: err.message || "An error occurred.",
            });
          },
        },
      );
    }
  };

  const handleDeleteGroup = () => {
    if (!deletingGroupId) return;

    deleteGroup(deletingGroupId, {
      onSuccess: () => {
        toaster.success({
          title: "Email group deleted",
          description: "The group has been successfully deleted.",
        });
        setIsDeleteDialogOpen(false);
        setDeletingGroupId(null);
      },
      onError: (err: Error) => {
        toaster.error({
          title: "Failed to delete email group",
          description: err.message || "An error occurred.",
        });
      },
    });
  };

  const columns: Column<EmailGroup>[] = [
    {
      header: "Group Name",
      accessor: "name",
      width: "250px",
    },
    {
      header: "Email Addresses",
      accessor: "email_addresses",
      render: (emails) => {
        const emailList = (emails as string[]) || [];
        return (
          <HStack gap={1} flexWrap="wrap">
            {emailList.map((email, idx) => (
              <Badge key={idx} variant="subtle" colorPalette="purple" size="sm">
                {email}
              </Badge>
            ))}
          </HStack>
        );
      },
    },
    {
      header: "Actions",
      accessor: "actions",
      textAlign: "center",
      width: "120px",
      render: (_, row) => (
        <HStack gap={2} justifyContent="center" w="100%">
          <IconButton
            aria-label="Edit group"
            variant="ghost"
            colorPalette="gray"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              handleOpenEditDialog(row);
            }}
          >
            <LuPencil />
          </IconButton>
          <IconButton
            aria-label="Delete group"
            variant="ghost"
            colorPalette="red"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              handleOpenDeleteDialog(row.id);
            }}
          >
            <LuTrash2 />
          </IconButton>
        </HStack>
      ),
    },
  ];

  return (
    <Flex direction="column" gap={4} w="100%" mt={2}>
      <Flex justifyContent="space-between" alignItems="center" w="100%">
        <Box fontWeight="bold" fontSize="lg" color="gray.700">
          Notification Groups
        </Box>
        <Button
          colorPalette="brand"
          size="sm"
          onClick={handleOpenAddDialog}
          gap={1}
        >
          <LuPlus /> Add Email Group
        </Button>
      </Flex>

      {isGroupsLoading ? (
        <LoadingSpinner />
      ) : !emailGroups || emailGroups.length === 0 ? (
        <Flex
          direction="column"
          alignItems="center"
          justifyContent="center"
          p={10}
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="lg"
          borderStyle="dashed"
          bg="gray.50"
          textAlign="center"
          gap={3}
        >
          <Text fontSize="lg" fontWeight="semibold" color="gray.600">
            No Email Groups Created Yet
          </Text>
          <Text fontSize="sm" color="gray.500" maxW="sm">
            Create groups to organize and send notifications to multiple team
            members at once.
          </Text>
          <Button
            colorPalette="brand"
            size="sm"
            onClick={handleOpenAddDialog}
            mt={2}
          >
            Create Your First Group
          </Button>
        </Flex>
      ) : (
        <Table<EmailGroup>
          data={emailGroups}
          columns={columns}
          totalElements={emailGroups.length}
          pageSize={100}
          updateCurrentPage={() => {}}
          isLoading={isGroupsLoading}
          hidePagination={true}
        />
      )}

      {/* Create / Edit Dialog */}
      <Dialog.Root
        open={isFormDialogOpen}
        onOpenChange={(e) => setIsFormDialogOpen(e.open)}
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content>
              <Dialog.Header>
                <Dialog.Title>
                  {editingGroup ? "Edit Email Group" : "Create Email Group"}
                </Dialog.Title>
              </Dialog.Header>
              <Dialog.Body>
                <Flex direction="column" gap={4}>
                  <Field.Root required>
                    <Field.Label>Group Name</Field.Label>
                    <Input
                      placeholder="e.g. DevOps Alerts"
                      value={groupName}
                      onChange={(e) => setGroupName(e.target.value)}
                    />
                  </Field.Root>
                  <Field.Root required>
                    <Field.Label>Email Addresses</Field.Label>
                    <Textarea
                      placeholder="email@developer.com; email@designer.com"
                      value={groupEmails}
                      onChange={(e) => setGroupEmails(e.target.value)}
                      rows={4}
                    />
                    <Field.HelperText>
                      Enter emails separated by Semicolons (;).
                    </Field.HelperText>
                  </Field.Root>
                </Flex>
              </Dialog.Body>
              <Dialog.Footer>
                <Dialog.ActionTrigger asChild>
                  <Button
                    variant="outline"
                    onClick={() => setIsFormDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </Dialog.ActionTrigger>
                <Button
                  colorPalette="brand"
                  loading={isCreating || isUpdating}
                  onClick={handleSaveGroup}
                >
                  Save
                </Button>
              </Dialog.Footer>
              <Dialog.CloseTrigger asChild>
                <CloseButton
                  size="sm"
                  onClick={() => setIsFormDialogOpen(false)}
                />
              </Dialog.CloseTrigger>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>

      {/* Delete Confirmation Dialog */}
      <Dialog.Root
        open={isDeleteDialogOpen}
        onOpenChange={(e) => setIsDeleteDialogOpen(e.open)}
        role="alertdialog"
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content>
              <Dialog.Header>
                <Dialog.Title>Delete confirmation</Dialog.Title>
              </Dialog.Header>
              <Dialog.Body>
                Are you sure you want to delete this email group? This action
                cannot be undone.
              </Dialog.Body>
              <Dialog.Footer>
                <Dialog.ActionTrigger asChild>
                  <Button
                    variant="outline"
                    onClick={() => setIsDeleteDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </Dialog.ActionTrigger>
                <Button
                  colorPalette="red"
                  loading={isDeleting}
                  onClick={handleDeleteGroup}
                >
                  Delete
                </Button>
              </Dialog.Footer>
              <Dialog.CloseTrigger asChild>
                <CloseButton
                  size="sm"
                  onClick={() => setIsDeleteDialogOpen(false)}
                />
              </Dialog.CloseTrigger>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    </Flex>
  );
};

const Email = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const pageTab =
    (searchParams.get("tab") as "dataload" | "notifications" | "templates") ||
    "dataload";

  const handleTabChange = (
    tabId: "dataload" | "notifications" | "templates",
  ) => {
    setSearchParams((prev) => {
      const nextParams = new URLSearchParams(prev);
      if (tabId === "dataload") {
        nextParams.delete("tab");
      } else {
        nextParams.set("tab", tabId);
      }
      return nextParams;
    });
  };

  return (
    <Flex flexDirection="column" gap={VIEW_CONFIG.pageGap} h="100%">
      <PageHeader
        breadcrumbs={[
          {
            label: "Account Settings",
            route: "",
          },
        ]}
        title="Communications"
      />
      <Box borderBottom="1px solid" borderColor="gray.100" mt={-4} />
      <Flex gap={8} mt={-4}>
        {[
          { id: "dataload", label: "Data Load" },
          { id: "notifications", label: "Notifications" },
          { id: "templates", label: "Email Templates" },
        ].map((tab) => (
          <Box
            key={tab.id}
            cursor="pointer"
            fontSize="md"
            fontWeight={pageTab === tab.id ? "700" : "500"}
            color={pageTab === tab.id ? "purple.600" : "gray.600"}
            position="relative"
            onClick={() =>
              handleTabChange(
                tab.id as "dataload" | "notifications" | "templates",
              )
            }
            pb={2}
            borderBottom="2px solid"
            borderColor={pageTab === tab.id ? "purple.600" : "transparent"}
          >
            {tab.label}
          </Box>
        ))}
      </Flex>

      {pageTab === "notifications" ? (
        <NotificationsTab />
      ) : pageTab === "templates" ? (
        <EmailTemplatesTab />
      ) : (
        <DataLoadTab />
      )}
    </Flex>
  );
};

export default Email;
