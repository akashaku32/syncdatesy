import { memo, useCallback, useDeferredValue, useMemo } from "react";

import {
  Box,
  Button,
  Flex,
  Grid,
  Input,
  NativeSelect,
  RadioGroup,
  Text,
} from "@chakra-ui/react";

import { LuTrash2 } from "react-icons/lu";

import DayOfWeekChips from "./DayOfWeekChips";
import DeferredRecurrenceSummary from "./DeferredRecurrenceSummary";
import MonthlyRecurrenceSection from "./MonthlyRecurrenceSection";
import TimezoneDateInput from "./TimezoneDateInput";
import TimezoneSelect from "./TimezoneSelect";
import {
  type DayCode,
  type MonthlyMode,
  type MonthlyWeekOccurrence,
  QUICK_PRESET_OPTIONS,
  RECURRENCE_PATTERN_OPTIONS,
  type RecurrencePattern,
  type ScheduleValue,
  applyQuickPreset,
  applyRecurrencePattern,
  mergeSyncStartDate,
  splitSyncStartDate,
  weeklySelectionValid,
} from "./scheduleOptions";

type ScheduleOnChange = React.Dispatch<React.SetStateAction<ScheduleValue>>;

interface ScheduleEditorProps {
  value: ScheduleValue;
  onChange: ScheduleOnChange;
  showStartDate?: boolean;
  showExecutionMode?: boolean;
  disabled?: boolean;
}

const ScheduleEditor = ({
  value,
  onChange,
  showStartDate = true,
  showExecutionMode = true,
  disabled = false,
}: ScheduleEditorProps) => {
  const { date: startDate, time: startTime } = useMemo(
    () => splitSyncStartDate(value.sync_start_date, value.schedule_config.time),
    [value.sync_start_date, value.schedule_config.time],
  );

  const pattern = value.schedule_config.recurrence_pattern;
  const weeklyInvalid = value.recurring && !weeklySelectionValid(value);
  const deferredValue = useDeferredValue(value);
  const scheduleTimezone = value.schedule_config.timezone || "UTC";

  const handleMonthlyRecurrencePatch = useCallback(
    (
      patch: Partial<{
        monthly_mode: MonthlyMode;
        day: number;
        monthly_week: MonthlyWeekOccurrence;
        monthly_weekday: DayCode;
        time: string;
      }>,
    ) => {
      onChange((prev) => {
        const { date } = splitSyncStartDate(
          prev.sync_start_date,
          prev.schedule_config.time,
        );
        const nextTime = patch.time ?? prev.schedule_config.time;
        return {
          ...prev,
          sync_start_date: patch.time
            ? mergeSyncStartDate(date, nextTime)
            : prev.sync_start_date,
          schedule_config: {
            ...prev.schedule_config,
            ...patch,
            time: nextTime,
          },
        };
      });
    },
    [onChange],
  );

  const handleTimezoneChange = useCallback(
    (timezone: string) => {
      onChange((prev) => ({
        ...prev,
        schedule_config: { ...prev.schedule_config, timezone },
      }));
    },
    [onChange],
  );

  const setRecurring = (recurring: boolean) => {
    onChange((prev) => ({
      ...prev,
      recurring,
      schedule_type: recurring
        ? prev.schedule_type === "manual"
          ? "interval"
          : prev.schedule_type
        : "manual",
    }));
  };

  const setStartDate = (date: string) => {
    onChange((prev) => ({
      ...prev,
      sync_start_date: date ? mergeSyncStartDate(date, startTime) : null,
    }));
  };

  const setStartTime = (time: string) => {
    onChange((prev) => ({
      ...prev,
      sync_start_date: mergeSyncStartDate(startDate, time),
      schedule_config: {
        ...prev.schedule_config,
        time,
      },
    }));
  };

  const setPattern = (nextPattern: RecurrencePattern) => {
    onChange((prev) => applyRecurrencePattern(prev, nextPattern));
  };

  return (
    <Flex direction="column" gap={4}>
      <TimezoneSelect
        value={value.schedule_config.timezone}
        onChange={handleTimezoneChange}
        disabled={disabled}
      />

      <Flex gap={3} wrap="wrap" alignItems="flex-start">
        {showStartDate && (
          <Box>
            <Text fontSize="xs" color="gray.600" mb={1}>
              Start date
            </Text>
            <TimezoneDateInput
              value={startDate}
              timezone={scheduleTimezone}
              disabled={disabled}
              onChange={setStartDate}
              showTimezoneHint={false}
            />
          </Box>
        )}
        <Box>
          <Text fontSize="xs" color="gray.600" mb={1}>
            Start time
          </Text>
          <Input
            type="time"
            size="sm"
            maxW="180px"
            disabled={disabled}
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
          />
        </Box>
      </Flex>

      <Box>
        <Button
          size="sm"
          variant={value.recurring ? "solid" : "outline"}
          colorPalette="brand"
          onClick={() => setRecurring(!value.recurring)}
          disabled={disabled}
        >
          Recurring
        </Button>
      </Box>

      {!value.recurring ? (
        <DeferredRecurrenceSummary value={deferredValue} />
      ) : (
        <Box
          borderWidth={1}
          borderColor="gray.200"
          borderRadius="md"
          p={4}
          bg="gray.50"
        >
          <Flex direction="column" gap={4}>
            <DeferredRecurrenceSummary value={deferredValue} />

            <Box>
              <Text fontSize="sm" fontWeight="semibold" mb={1}>
                Repeat
              </Text>
              <NativeSelect.Root size="sm" maxW="320px" disabled={disabled}>
                <NativeSelect.Field
                  value={pattern}
                  onChange={(e) =>
                    setPattern(e.target.value as RecurrencePattern)
                  }
                >
                  {RECURRENCE_PATTERN_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </Box>

            {pattern === "every_n_minutes" && (
              <Flex alignItems="center" gap={2} wrap="wrap">
                <Text fontSize="sm" fontWeight="semibold" whiteSpace="nowrap">
                  Every
                </Text>
                <Input
                  type="number"
                  size="sm"
                  min={1}
                  max={999}
                  w="72px"
                  disabled={disabled}
                  value={value.schedule_config.interval_value}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      schedule_config: {
                        ...value.schedule_config,
                        interval_value: Math.max(
                          1,
                          Number(e.target.value) || 1,
                        ),
                      },
                    })
                  }
                />
                <NativeSelect.Root size="sm" w="120px" disabled={disabled}>
                  <NativeSelect.Field
                    value={
                      value.schedule_config.interval_unit === "hour"
                        ? "hour"
                        : "minute"
                    }
                    onChange={(e) =>
                      onChange({
                        ...value,
                        schedule_config: {
                          ...value.schedule_config,
                          interval_unit:
                            e.target.value === "hour" ? "hour" : "minute",
                        },
                      })
                    }
                  >
                    <option value="minute">minute(s)</option>
                    <option value="hour">hour(s)</option>
                  </NativeSelect.Field>
                  <NativeSelect.Indicator />
                </NativeSelect.Root>
              </Flex>
            )}

            {pattern === "weekly" && (
              <Flex alignItems="center" gap={2} wrap="wrap">
                <Text fontSize="sm" fontWeight="semibold" whiteSpace="nowrap">
                  Repeat every
                </Text>
                <Input
                  type="number"
                  size="sm"
                  min={1}
                  max={52}
                  w="72px"
                  disabled={disabled}
                  value={value.schedule_config.interval_value}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      schedule_config: {
                        ...value.schedule_config,
                        interval_value: Math.max(
                          1,
                          Number(e.target.value) || 1,
                        ),
                      },
                    })
                  }
                />
                <Text fontSize="sm" color="gray.600">
                  week(s) on
                </Text>
              </Flex>
            )}

            {pattern === "weekly" && (
              <DayOfWeekChips
                value={value.schedule_config.days_of_week}
                onChange={(days_of_week) =>
                  onChange({
                    ...value,
                    schedule_config: {
                      ...value.schedule_config,
                      days_of_week,
                      day_of_week: days_of_week[0] ?? "mon",
                    },
                  })
                }
                disabled={disabled}
              />
            )}

            {weeklyInvalid && (
              <Text fontSize="xs" color="red.500">
                Select at least one day of the week.
              </Text>
            )}

            {pattern === "monthly" && (
              <MonthlyRecurrenceSection
                monthlyMode={value.schedule_config.monthly_mode}
                dayOfMonth={value.schedule_config.day}
                monthlyWeek={value.schedule_config.monthly_week}
                monthlyWeekday={value.schedule_config.monthly_weekday}
                runAtTime={value.schedule_config.time}
                onChange={handleMonthlyRecurrencePatch}
                disabled={disabled}
              />
            )}

            {pattern !== "every_n_minutes" && pattern !== "monthly" && (
              <Box>
                <Text fontSize="xs" color="gray.600" mb={1}>
                  Run at
                </Text>
                <Input
                  type="time"
                  size="sm"
                  maxW="160px"
                  disabled={disabled}
                  value={value.schedule_config.time}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      schedule_config: {
                        ...value.schedule_config,
                        time: e.target.value,
                      },
                    })
                  }
                />
              </Box>
            )}

            <Flex direction="column" gap={2}>
              <Text fontSize="sm" fontWeight="semibold">
                Until
              </Text>
              <Flex alignItems="center" gap={2} wrap="wrap">
                <RadioGroup.Root
                  value={value.end_mode}
                  onValueChange={({ value: mode }) =>
                    !disabled &&
                    mode &&
                    onChange({
                      ...value,
                      end_mode: mode as "never" | "on_date",
                      sync_end_date:
                        mode === "on_date" ? value.sync_end_date : null,
                    })
                  }
                  disabled={disabled}
                >
                  <Flex gap={4}>
                    <RadioGroup.Item value="never">
                      <RadioGroup.ItemHiddenInput />
                      <RadioGroup.ItemIndicator />
                      <RadioGroup.ItemText>Never</RadioGroup.ItemText>
                    </RadioGroup.Item>
                    <RadioGroup.Item value="on_date">
                      <RadioGroup.ItemHiddenInput />
                      <RadioGroup.ItemIndicator />
                      <RadioGroup.ItemText>On date</RadioGroup.ItemText>
                    </RadioGroup.Item>
                  </Flex>
                </RadioGroup.Root>
                {value.end_mode === "on_date" && (
                  <>
                    <TimezoneDateInput
                      value={value.sync_end_date?.slice(0, 10) ?? ""}
                      timezone={scheduleTimezone}
                      disabled={disabled}
                      maxW="180px"
                      onChange={(iso) =>
                        onChange({
                          ...value,
                          sync_end_date: iso ? `${iso}T23:59:59` : null,
                        })
                      }
                    />
                    <Button
                      size="xs"
                      variant="ghost"
                      colorPalette="gray"
                      disabled={disabled || !value.sync_end_date}
                      onClick={() =>
                        onChange({
                          ...value,
                          sync_end_date: null,
                          end_mode: "never",
                        })
                      }
                      aria-label="Clear end date"
                    >
                      <LuTrash2 />
                    </Button>
                  </>
                )}
              </Flex>
            </Flex>

            {pattern === "every_n_minutes" && (
              <Flex direction="column" gap={2}>
                <Text fontSize="sm" fontWeight="semibold">
                  Quick presets
                </Text>
                <Grid templateColumns="repeat(4, 1fr)" gap={2}>
                  {QUICK_PRESET_OPTIONS.map((opt) => {
                    const isSelected =
                      value.schedule_config.interval_unit === "minute" &&
                      value.schedule_config.interval_value === opt.value;
                    return (
                      <Button
                        key={opt.value}
                        type="button"
                        onClick={() => {
                          if (disabled) return;
                          onChange(applyQuickPreset(value, opt.value));
                        }}
                        px={2}
                        py={1.5}
                        h="auto"
                        borderRadius="md"
                        variant={isSelected ? "solid" : "outline"}
                        colorPalette={isSelected ? "brand" : "gray"}
                        fontSize="xs"
                        fontWeight={isSelected ? "semibold" : "normal"}
                        disabled={disabled}
                      >
                        {opt.label}
                      </Button>
                    );
                  })}
                </Grid>
              </Flex>
            )}
          </Flex>
        </Box>
      )}

      {showExecutionMode && (
        <Flex direction="column" gap={2}>
          <Text fontSize="sm" fontWeight="semibold">
            Execution mode (tables in batch)
          </Text>
          <RadioGroup.Root
            value={value.execution_order}
            onValueChange={({ value: v }) =>
              !disabled &&
              v &&
              onChange({
                ...value,
                execution_order: v as "parallel" | "sequential",
              })
            }
            disabled={disabled}
          >
            <Flex gap={4}>
              <RadioGroup.Item value="parallel">
                <RadioGroup.ItemHiddenInput />
                <RadioGroup.ItemIndicator />
                <RadioGroup.ItemText>Parallel</RadioGroup.ItemText>
              </RadioGroup.Item>
              <RadioGroup.Item value="sequential">
                <RadioGroup.ItemHiddenInput />
                <RadioGroup.ItemIndicator />
                <RadioGroup.ItemText>Sequential</RadioGroup.ItemText>
              </RadioGroup.Item>
            </Flex>
          </RadioGroup.Root>
        </Flex>
      )}
    </Flex>
  );
};

export default memo(ScheduleEditor);
