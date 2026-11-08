import {
  Checkbox,
  Field,
  Grid,
  HStack,
  Input,
  NativeSelect,
  VStack,
} from "@chakra-ui/react";

import { MultiColumnSorting } from "../components/MultiColumnSorting";
import { type BasicOptionsTabProps } from "../helpers";

export default function BasicOptionsTab({
  options,
  sheetName,
  colNames,
  updateOptions,
}: BasicOptionsTabProps) {
  return (
    <VStack gap={2.5} align="stretch">
      {/* Row 1: Left 50% (Sheet Name 75%, Freeze Panes 25%) and Right 50% (Checkboxes 1/3 each) */}
      <Grid
        templateColumns={{ base: "1fr", md: "1fr 1fr" }}
        gap={2}
        alignItems="end"
      >
        {/* Left 50%: Sheet Name takes 75%, Freeze Panes takes 25% */}
        <Grid templateColumns="3fr 1fr" gap={2} alignItems="end">
          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Sheet Name
            </Field.Label>
            <Input
              size="xs"
              h="28px"
              bg="white"
              value={sheetName}
              onChange={(e) => updateOptions({ sheet_name: e.target.value })}
              placeholder="Sheet1"
            />
          </Field.Root>

          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Freeze Panes
            </Field.Label>
            <Input
              size="xs"
              h="28px"
              bg="white"
              value={options.freeze_panes ?? ""}
              onChange={(e) => updateOptions({ freeze_panes: e.target.value })}
              placeholder="A2"
            />
          </Field.Root>
        </Grid>

        {/* Right 50%: Three checkboxes, vertically centered on input height with compact gap */}
        <HStack
          gap={2.5}
          h="28px"
          align="center"
          wrap={{ base: "wrap", md: "nowrap" }}
        >
          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={options.auto_filter ?? true}
            onCheckedChange={(details) =>
              updateOptions({ auto_filter: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label
              fontSize="xs"
              fontWeight="medium"
              color="gray.700"
              whiteSpace="nowrap"
            >
              Auto Filter
            </Checkbox.Label>
          </Checkbox.Root>

          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={options.auto_width ?? true}
            onCheckedChange={(details) =>
              updateOptions({ auto_width: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label
              fontSize="xs"
              fontWeight="medium"
              color="gray.700"
              whiteSpace="nowrap"
            >
              Auto Column Width
            </Checkbox.Label>
          </Checkbox.Root>

          <Checkbox.Root
            size="xs"
            colorPalette="brand"
            checked={options.freeze_header ?? true}
            onCheckedChange={(details) =>
              updateOptions({ freeze_header: !!details.checked })
            }
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label
              fontSize="xs"
              fontWeight="medium"
              color="gray.700"
              whiteSpace="nowrap"
            >
              Freeze Header Row
            </Checkbox.Label>
          </Checkbox.Root>
        </HStack>
      </Grid>

      {/* Row 2: Formats */}
      <Grid templateColumns="repeat(3, 1fr)" gap={2.5} alignItems="end">
        <Field.Root gap={0}>
          <Field.Label
            fontSize="xs"
            fontWeight="semibold"
            color="gray.600"
            mb={0.5}
          >
            Date Format
          </Field.Label>
          <NativeSelect.Root size="xs">
            <NativeSelect.Field
              bg="white"
              h="28px"
              value={options.date_format ?? "yyyy-mm-dd"}
              onChange={(e) => updateOptions({ date_format: e.target.value })}
            >
              <option value="yyyy-mm-dd">YYYY-MM-DD</option>
              <option value="dd-mm-yyyy">DD-MM-YYYY</option>
              <option value="mm/dd/yyyy">MM/DD/YYYY</option>
              <option value="d-mmm-yy">D-MMM-YY</option>
              <option value="mmmm d, yyyy">MMMM D, YYYY</option>
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>

        <Field.Root gap={0}>
          <Field.Label
            fontSize="xs"
            fontWeight="semibold"
            color="gray.600"
            mb={0.5}
          >
            Datetime Format
          </Field.Label>
          <NativeSelect.Root size="xs">
            <NativeSelect.Field
              bg="white"
              h="28px"
              value={options.datetime_format ?? "yyyy-mm-dd hh:mm:ss"}
              onChange={(e) =>
                updateOptions({ datetime_format: e.target.value })
              }
            >
              <option value="yyyy-mm-dd hh:mm:ss">YYYY-MM-DD HH:mm:ss</option>
              <option value="yyyy-mm-dd hh:mm:ss AM/PM">
                YYYY-MM-DD HH:mm:ss AM/PM
              </option>
              <option value="mm/dd/yyyy hh:mm:ss">MM/DD/YYYY HH:mm:ss</option>
              <option value="dd-mm-yyyy hh:mm:ss">DD-MM-YYYY HH:mm:ss</option>
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>

        <Field.Root gap={0}>
          <Field.Label
            fontSize="xs"
            fontWeight="semibold"
            color="gray.600"
            mb={0.5}
          >
            Time Format
          </Field.Label>
          <NativeSelect.Root size="xs">
            <NativeSelect.Field
              bg="white"
              h="28px"
              value={options.time_format ?? "hh:mm:ss"}
              onChange={(e) => updateOptions({ time_format: e.target.value })}
            >
              <option value="hh:mm:ss">HH:mm:ss</option>
              <option value="hh:mm">HH:mm</option>
              <option value="hh:mm:ss AM/PM">HH:mm:ss AM/PM</option>
              <option value="hh:mm AM/PM">HH:mm AM/PM</option>
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>
      </Grid>

      {/* Row 3: Multi-Column Sorting */}
      <MultiColumnSorting
        options={options}
        colNames={colNames}
        updateOptions={updateOptions}
      />
    </VStack>
  );
}
