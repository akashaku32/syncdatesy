import { memo, useEffect, useState } from "react";

import { Box, Input, Text } from "@chakra-ui/react";

import {
  dateFnsPatternForTimezone,
  displayToIsoDate,
  isoDateToDisplay,
} from "./scheduleOptions";

interface TimezoneDateInputProps {
  value: string;
  onChange: (_isoDate: string) => void;
  timezone: string;
  disabled?: boolean;
  maxW?: string;
  showTimezoneHint?: boolean;
}

const TimezoneDateInput = ({
  value,
  onChange,
  timezone,
  disabled = false,
  maxW = "180px",
  showTimezoneHint = true,
}: TimezoneDateInputProps) => {
  const pattern = dateFnsPatternForTimezone(timezone);
  const [text, setText] = useState(() => isoDateToDisplay(value, timezone));

  useEffect(() => {
    setText(isoDateToDisplay(value, timezone));
  }, [value, timezone]);

  const commit = () => {
    const iso = displayToIsoDate(text, timezone);
    if (iso) {
      onChange(iso);
      setText(isoDateToDisplay(iso, timezone));
      return;
    }
    if (!text.trim()) {
      onChange("");
      return;
    }
    setText(isoDateToDisplay(value, timezone));
  };

  return (
    <Box>
      <Input
        size="sm"
        maxW={maxW}
        disabled={disabled}
        value={text}
        placeholder={pattern}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
      />
      {showTimezoneHint && (
        <Text fontSize="2xs" color="gray.500" mt={0.5}>
          {`${pattern} · ${timezone}`}
        </Text>
      )}
    </Box>
  );
};

export default memo(TimezoneDateInput);
