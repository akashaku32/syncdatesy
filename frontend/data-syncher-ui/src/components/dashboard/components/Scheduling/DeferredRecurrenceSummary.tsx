import { memo, useDeferredValue } from "react";

import RecurrenceSummary from "./RecurrenceSummary";
import { type ScheduleValue } from "./scheduleOptions";

interface DeferredRecurrenceSummaryProps {
  value: ScheduleValue;
}

const DeferredRecurrenceSummary = ({
  value,
}: DeferredRecurrenceSummaryProps) => {
  const deferred = useDeferredValue(value);
  return <RecurrenceSummary value={deferred} />;
};

export default memo(DeferredRecurrenceSummary);
