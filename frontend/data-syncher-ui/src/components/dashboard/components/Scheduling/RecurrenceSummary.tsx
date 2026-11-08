import { memo } from "react";

import { Text } from "@chakra-ui/react";

import { type ScheduleValue, formatRecurrenceSummary } from "./scheduleOptions";

interface RecurrenceSummaryProps {
  value: ScheduleValue;
}

const RecurrenceSummary = ({ value }: RecurrenceSummaryProps) => (
  <Text fontSize="sm" color="gray.600" lineHeight="1.5">
    {formatRecurrenceSummary(value)}
  </Text>
);

export default memo(RecurrenceSummary);
