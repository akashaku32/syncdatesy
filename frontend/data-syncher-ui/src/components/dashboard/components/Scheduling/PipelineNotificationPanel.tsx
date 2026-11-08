import { useEffect, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  Field,
  Flex,
  Input,
  Switch,
  Text,
  VStack,
} from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import useFetchEmailGroups from "@/queryOptions/emailGroups/useFetchEmailGroups";
import { usePatchPipeline } from "@/queryOptions/pipeline/usePipeline";
import { type PipelineDetail } from "@/types/pipeline";

import {
  formatDirectEmailsInput,
  parseDirectEmailsInput,
} from "./notificationOptions";

type PipelineNotificationPanelProps = {
  pipeline: PipelineDetail;
  disabled?: boolean;
  embedded?: boolean;
};

const PipelineNotificationPanel = ({
  pipeline,
  disabled = false,
  embedded = false,
}: PipelineNotificationPanelProps) => {
  const patchPipeline = usePatchPipeline(pipeline.id);
  const { data: emailGroups = [], isLoading: groupsLoading } =
    useFetchEmailGroups();

  const [notifyOnFlowFinish, setNotifyOnFlowFinish] = useState(
    pipeline.notify_on_flow_finish ?? true,
  );
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>(
    pipeline.notification_email_group_ids ?? [],
  );
  const [directEmailsInput, setDirectEmailsInput] = useState(() =>
    formatDirectEmailsInput(pipeline.notification_emails),
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync form when pipeline notification settings change
    setNotifyOnFlowFinish(pipeline.notify_on_flow_finish ?? true);
    setSelectedGroupIds(pipeline.notification_email_group_ids ?? []);
    setDirectEmailsInput(formatDirectEmailsInput(pipeline.notification_emails));
  }, [pipeline]);

  const toggleGroup = (groupId: number) => {
    setSelectedGroupIds((prev) =>
      prev.includes(groupId)
        ? prev.filter((id) => id !== groupId)
        : [...prev, groupId],
    );
  };

  const handleSave = async () => {
    try {
      const notificationEmails = parseDirectEmailsInput(directEmailsInput);
      await patchPipeline.mutateAsync({
        notify_on_flow_finish: notifyOnFlowFinish,
        notification_email_group_ids: selectedGroupIds,
        notification_emails: notificationEmails,
      });
      toaster.success({ title: "Notification settings saved" });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not save notifications";
      toaster.error({ title: message });
    }
  };

  return (
    <Box
      mt={embedded ? 0 : 4}
      pt={embedded ? 0 : 4}
      borderTopWidth={embedded ? 0 : 1}
      borderColor="gray.100"
    >
      {!embedded && (
        <>
          <Text fontSize="sm" fontWeight="semibold" mb={1}>
            Email alerts
          </Text>
          <Text fontSize="xs" color="gray.600" mb={3}>
            Recipients for pipeline notifications. Enable per-batch alerts on
            each batch node.
          </Text>
        </>
      )}
      {embedded && (
        <Text fontSize="xs" color="gray.600" mb={3}>
          Flow-finish alerts use these recipients. Per-batch emails are enabled
          on each batch&apos;s Email tab.
        </Text>
      )}

      <Flex align="center" justify="space-between" mb={3} gap={2}>
        <Box flex="1" minW={0}>
          <Text fontSize={embedded ? "xs" : "sm"} fontWeight="medium">
            Notify when pipeline finishes
          </Text>
          <Text fontSize="2xs" color="gray.500">
            On success or failure
          </Text>
        </Box>
        <Switch.Root
          checked={notifyOnFlowFinish}
          onCheckedChange={({ checked }) => setNotifyOnFlowFinish(!!checked)}
          disabled={disabled}
          colorPalette="brand"
        >
          <Switch.HiddenInput />
          <Switch.Control />
        </Switch.Root>
      </Flex>

      <Text fontSize="xs" fontWeight="medium" color="gray.700" mb={2}>
        Email groups
      </Text>
      {groupsLoading && (
        <Text fontSize="xs" color="gray.500" mb={2}>
          Loading groups...
        </Text>
      )}
      {!groupsLoading && emailGroups.length === 0 && (
        <Text fontSize="xs" color="gray.500" mb={2}>
          No email groups yet. Create them in Account Settings.
        </Text>
      )}
      <VStack align="stretch" gap={1.5} mb={3} maxH="120px" overflowY="auto">
        {emailGroups.map((group) => (
          <Checkbox.Root
            key={group.id}
            checked={selectedGroupIds.includes(group.id)}
            onCheckedChange={() => toggleGroup(group.id)}
            disabled={disabled}
            size="sm"
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="sm">
              {group.name}
              <Text as="span" fontSize="xs" color="gray.500" ml={1}>
                ({group.email_addresses?.length ?? 0} emails)
              </Text>
            </Checkbox.Label>
          </Checkbox.Root>
        ))}
      </VStack>

      <Field.Root mb={3}>
        <Field.Label fontSize="xs">Additional email addresses</Field.Label>
        <Input
          size="sm"
          value={directEmailsInput}
          onChange={(e) => setDirectEmailsInput(e.target.value)}
          placeholder="ops@company.com, admin@company.com"
          disabled={disabled}
        />
        <Field.HelperText fontSize="xs">
          Comma-separated. Combined with selected email groups.
        </Field.HelperText>
      </Field.Root>

      <Button
        size="sm"
        colorPalette="brand"
        w="full"
        onClick={handleSave}
        loading={patchPipeline.isPending}
        disabled={disabled}
      >
        Save{embedded ? "" : " notifications"}
      </Button>
    </Box>
  );
};

export default PipelineNotificationPanel;
