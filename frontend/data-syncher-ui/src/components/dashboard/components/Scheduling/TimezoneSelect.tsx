import { memo, useMemo, useState } from "react";

import { Box, Input, NativeSelect, Text } from "@chakra-ui/react";

import {
  STANDARD_TIMEZONES,
  filterTimezonesForSearch,
  formatTimezoneLabel,
} from "./timezoneOptions";

interface TimezoneSelectProps {
  value: string;
  onChange: (_timezone: string) => void;
  disabled?: boolean;
}

const TimezoneSelect = ({
  value,
  onChange,
  disabled = false,
}: TimezoneSelectProps) => {
  const [filter, setFilter] = useState("");

  const localTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  }, []);

  const selected = value || "UTC";
  const query = filter.trim();

  const searchResults = useMemo(
    () => filterTimezonesForSearch(query, selected),
    [query, selected],
  );

  const showLocalGroup =
    !query &&
    localTimezone &&
    localTimezone !== selected &&
    !STANDARD_TIMEZONES.some((tz) => tz.iana === localTimezone);

  const showSelectedGroup =
    !query &&
    selected &&
    !STANDARD_TIMEZONES.some((tz) => tz.iana === selected);

  return (
    <Box w="full">
      <Text fontSize="xs" color="gray.600" mb={1}>
        Time zone
      </Text>
      <Input
        size="sm"
        w="full"
        mb={1}
        placeholder="Search time zones (e.g. Kolkata, UTC+05:30)"
        disabled={disabled}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        aria-label="Search time zones"
      />
      {query ? (
        <Text fontSize="2xs" color="gray.500" mb={1.5}>
          {searchResults.length === 0
            ? "No matches — try a city name or UTC offset"
            : `${searchResults.length} match${searchResults.length === 1 ? "" : "es"} — choose below`}
        </Text>
      ) : (
        <Text fontSize="2xs" color="gray.500" mb={1.5}>
          Search to filter, or pick from standard time zones below
        </Text>
      )}
      <NativeSelect.Root size="sm" disabled={disabled} w="full">
        <NativeSelect.Field
          value={selected}
          onChange={(e) => {
            onChange(e.target.value);
            setFilter("");
          }}
        >
          {query ? (
            <>
              {searchResults.length === 0 && (
                <option value={selected} disabled>
                  No matches
                </option>
              )}
              {searchResults.map(({ iana, label }) => (
                <option key={iana} value={iana}>
                  {label}
                </option>
              ))}
            </>
          ) : (
            <>
              {showLocalGroup && (
                <optgroup label="Local time zone">
                  <option value={localTimezone}>
                    {formatTimezoneLabel(localTimezone)} (local)
                  </option>
                </optgroup>
              )}
              {showSelectedGroup && (
                <optgroup label="Current selection">
                  <option value={selected}>
                    {formatTimezoneLabel(selected)}
                  </option>
                </optgroup>
              )}
              <optgroup label="Standard time zones">
                {STANDARD_TIMEZONES.map((tz) => (
                  <option key={tz.iana} value={tz.iana}>
                    {formatTimezoneLabel(tz.iana, tz.name)}
                    {tz.iana === localTimezone ? " (local)" : ""}
                  </option>
                ))}
              </optgroup>
            </>
          )}
        </NativeSelect.Field>
        <NativeSelect.Indicator />
      </NativeSelect.Root>
    </Box>
  );
};

export default memo(TimezoneSelect);
