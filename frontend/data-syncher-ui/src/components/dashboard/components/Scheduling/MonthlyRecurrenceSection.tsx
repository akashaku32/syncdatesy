import {
  type CSSProperties,
  memo,
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Box, Flex, Text } from "@chakra-ui/react";

import {
  DAY_CODES,
  type DayCode,
  type MonthlyMode,
  type MonthlyWeekOccurrence,
  dayCodeLabel,
} from "./scheduleOptions";

const WEEK_OCCURRENCES: { value: MonthlyWeekOccurrence; label: string }[] = [
  { value: "first", label: "first" },
  { value: "second", label: "second" },
  { value: "third", label: "third" },
  { value: "fourth", label: "fourth" },
  { value: "last", label: "last" },
];

const selectStyle: CSSProperties = {
  fontSize: "14px",
  lineHeight: "1.25",
  padding: "4px 28px 4px 8px",
  borderRadius: "6px",
  border: "1px solid var(--chakra-colors-gray-200)",
  backgroundColor: "white",
  cursor: "pointer",
  maxWidth: "100%",
};

const inputStyle: CSSProperties = {
  fontSize: "14px",
  lineHeight: "1.25",
  padding: "4px 8px",
  borderRadius: "6px",
  border: "1px solid var(--chakra-colors-gray-200)",
  backgroundColor: "white",
  width: "72px",
};

const radioStyle: CSSProperties = {
  margin: 0,
  cursor: "pointer",
};

type MonthlyPatch = Partial<{
  monthly_mode: MonthlyMode;
  day: number;
  monthly_week: MonthlyWeekOccurrence;
  monthly_weekday: DayCode;
  time: string;
}>;

interface MonthlyRecurrenceSectionProps {
  monthlyMode: MonthlyMode;
  dayOfMonth: number;
  monthlyWeek: MonthlyWeekOccurrence;
  monthlyWeekday: DayCode;
  runAtTime: string;
  onChange: (_patch: MonthlyPatch) => void;
  disabled?: boolean;
}

type LocalState = {
  monthlyMode: MonthlyMode;
  dayOfMonth: number;
  monthlyWeek: MonthlyWeekOccurrence;
  monthlyWeekday: DayCode;
  runAtTime: string;
};

const DEBOUNCE_MS = 350;

const MonthlyRecurrenceSection = ({
  monthlyMode,
  dayOfMonth,
  monthlyWeek,
  monthlyWeekday,
  runAtTime,
  onChange,
  disabled = false,
}: MonthlyRecurrenceSectionProps) => {
  const [local, setLocal] = useState<LocalState>({
    monthlyMode,
    dayOfMonth,
    monthlyWeek,
    monthlyWeekday,
    runAtTime,
  });

  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingPatchRef = useRef<MonthlyPatch>({});

  const flush = useCallback((immediate = false) => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    const patch = pendingPatchRef.current;
    if (Object.keys(patch).length === 0) return;
    pendingPatchRef.current = {};
    const run = () => onChangeRef.current(patch);
    if (immediate) {
      startTransition(run);
    } else {
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        startTransition(run);
      }, DEBOUNCE_MS);
    }
  }, []);

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const apply = (patch: MonthlyPatch, immediate = false) => {
    setLocal((prev) => ({
      monthlyMode: patch.monthly_mode ?? prev.monthlyMode,
      dayOfMonth: patch.day ?? prev.dayOfMonth,
      monthlyWeek: patch.monthly_week ?? prev.monthlyWeek,
      monthlyWeekday: patch.monthly_weekday ?? prev.monthlyWeekday,
      runAtTime: patch.time ?? prev.runAtTime,
    }));
    pendingPatchRef.current = { ...pendingPatchRef.current, ...patch };
    flush(immediate);
  };

  return (
    <Flex direction="column" gap={4}>
      <Flex direction="column" gap={3}>
        <Text fontSize="sm" fontWeight="semibold">
          Monthly pattern
        </Text>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            cursor: disabled ? "default" : "pointer",
          }}
        >
          <input
            type="radio"
            name="monthly_mode"
            style={radioStyle}
            disabled={disabled}
            checked={local.monthlyMode === "day_of_month"}
            onChange={() => apply({ monthly_mode: "day_of_month" }, true)}
          />
          <Text fontSize="sm" as="span">
            Day
          </Text>
          <input
            type="number"
            min={1}
            max={31}
            style={inputStyle}
            disabled={disabled || local.monthlyMode !== "day_of_month"}
            value={local.dayOfMonth}
            onChange={(e) =>
              apply({
                day: Math.min(31, Math.max(1, Number(e.target.value) || 1)),
              })
            }
            onBlur={() => flush(true)}
          />
          <Text fontSize="sm" color="gray.600" as="span">
            of every month
          </Text>
        </label>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            cursor: disabled ? "default" : "pointer",
          }}
        >
          <input
            type="radio"
            name="monthly_mode"
            style={radioStyle}
            disabled={disabled}
            checked={local.monthlyMode === "relative"}
            onChange={() => apply({ monthly_mode: "relative" }, true)}
          />
          <Text fontSize="sm" as="span">
            The
          </Text>
          <select
            style={{ ...selectStyle, width: "110px" }}
            disabled={disabled || local.monthlyMode !== "relative"}
            value={local.monthlyWeek}
            onChange={(e) =>
              apply(
                { monthly_week: e.target.value as MonthlyWeekOccurrence },
                true,
              )
            }
          >
            {WEEK_OCCURRENCES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            style={{ ...selectStyle, width: "140px" }}
            disabled={disabled || local.monthlyMode !== "relative"}
            value={local.monthlyWeekday}
            onChange={(e) =>
              apply({ monthly_weekday: e.target.value as DayCode }, true)
            }
          >
            {DAY_CODES.map((code) => (
              <option key={code} value={code}>
                {dayCodeLabel(code)}
              </option>
            ))}
          </select>
          <Text fontSize="sm" color="gray.600" as="span">
            of every month
          </Text>
        </label>
      </Flex>

      <Box>
        <Text fontSize="xs" color="gray.600" mb={1}>
          Run at
        </Text>
        <input
          type="time"
          style={{ ...inputStyle, width: "160px" }}
          disabled={disabled}
          value={local.runAtTime}
          onChange={(e) => apply({ time: e.target.value })}
          onBlur={() => flush(true)}
        />
      </Box>
    </Flex>
  );
};

export default memo(MonthlyRecurrenceSection);
