import {
  Box,
  Checkbox,
  Field,
  Flex,
  Grid,
  HStack,
  Input,
  NativeSelect,
  Text,
  VStack,
} from "@chakra-ui/react";

import ColorPicker from "@/components/shared/ColorPicker";
import {
  type ExcelConditionItem,
  type ExcelConditionalFormat,
} from "@/types/connectors";

import MultipleConditionsRule from "../../components/MultipleConditionsRule";
import {
  type ColumnDataType,
  getValidationError,
  isOperatorAllowed,
  stripFormulaQuotes,
} from "../../helpers";

interface RuleTypeConfigProps {
  rule: ExcelConditionalFormat;
  typeNorm: string;
  colDataType: ColumnDataType;
  colNames: string[];
  tableFields: Record<string, string>;
  onUpdateRule: (_patch: Partial<ExcelConditionalFormat>) => void;
}

export default function RuleTypeConfig({
  rule,
  typeNorm,
  colDataType,
  colNames,
  tableFields,
  onUpdateRule,
}: RuleTypeConfigProps) {
  const minVal = stripFormulaQuotes(
    Array.isArray(rule.formula)
      ? (rule.formula[0] ?? "")
      : (rule.formula ?? ""),
  );
  const minValErr = getValidationError(minVal, colDataType);

  const maxVal = stripFormulaQuotes(
    Array.isArray(rule.formula) ? (rule.formula[1] ?? "") : "",
  );
  const maxValErr = getValidationError(maxVal, colDataType);

  const singleVal = Array.isArray(rule.formula)
    ? rule.formula.map(stripFormulaQuotes).join(",")
    : stripFormulaQuotes(rule.formula ?? "");
  const singleValErr = getValidationError(singleVal, colDataType);

  const formulaExpr = Array.isArray(rule.formula)
    ? (rule.formula[0] ?? "")
    : (rule.formula ?? "");
  const formulaExprErr =
    formulaExpr && !formulaExpr.trim().startsWith("=")
      ? "Formula must start with '=' (e.g. =A2>100)"
      : null;

  const getIconSetPreview = (style: string) => {
    const normStyle = (style || "3TrafficLights1").toLowerCase();
    switch (normStyle) {
      case "3trafficlights1":
        return (
          <HStack gap={1} mt={1.5}>
            <Box
              w="10px"
              h="10px"
              borderRadius="full"
              bg="red.500"
              title="Low"
            />
            <Box
              w="10px"
              h="10px"
              borderRadius="full"
              bg="yellow.500"
              title="Medium"
            />
            <Box
              w="10px"
              h="10px"
              borderRadius="full"
              bg="green.500"
              title="High"
            />
            <Text fontSize="10px" color="gray.500" ml={1}>
              (Red / Yellow / Green Lights)
            </Text>
          </HStack>
        );
      case "3arrows":
        return (
          <HStack gap={1} mt={1.5} fontSize="10px" fontWeight="bold">
            <Text color="red.500">⬇</Text>
            <Text color="yellow.500">➡</Text>
            <Text color="green.500">⬆</Text>
            <Text color="gray.500" ml={1} fontWeight="normal">
              (Red Down / Yellow Right / Green Up)
            </Text>
          </HStack>
        );
      case "3flags":
        return (
          <HStack gap={1} mt={1.5} fontSize="10px">
            <Text color="red.500">🚩</Text>
            <Text color="yellow.500">🚩</Text>
            <Text color="green.500">🚩</Text>
            <Text color="gray.500" ml={1}>
              (Red / Yellow / Green Flags)
            </Text>
          </HStack>
        );
      case "3symbols":
        return (
          <HStack gap={1} mt={1.5} fontSize="10px" fontWeight="bold">
            <Text color="red.500">✖</Text>
            <Text color="yellow.500">⚠</Text>
            <Text color="green.500">✔</Text>
            <Text color="gray.500" ml={1} fontWeight="normal">
              (Red Cross / Yellow Warning / Green Check)
            </Text>
          </HStack>
        );
      case "4arrows":
        return (
          <HStack gap={1} mt={1.5} fontSize="10px" fontWeight="bold">
            <Text color="red.500">⬇</Text>
            <Text color="yellow.500">↘</Text>
            <Text color="yellow.500">↗</Text>
            <Text color="green.500">⬆</Text>
            <Text color="gray.500" ml={1} fontWeight="normal">
              (4-Directional colored arrows)
            </Text>
          </HStack>
        );
      case "5arrows":
        return (
          <HStack gap={1} mt={1.5} fontSize="10px" fontWeight="bold">
            <Text color="red.500">⬇</Text>
            <Text color="yellow.500">↘</Text>
            <Text color="yellow.500">➡</Text>
            <Text color="yellow.500">↗</Text>
            <Text color="green.500">⬆</Text>
            <Text color="gray.500" ml={1} fontWeight="normal">
              (5-Directional colored arrows)
            </Text>
          </HStack>
        );
      default:
        return null;
    }
  };

  return (
    <Box
      w="100%"
      minW="0"
      {...(typeNorm === "multipleconditions"
        ? {
            p: 1.5,
            bg: "gray.50",
            borderRadius: "md",
            border: "1px solid",
            borderColor: "gray.200",
          }
        : {})}
    >
      {typeNorm === "multipleconditions" && (
        <MultipleConditionsRule
          conditions={rule.conditions}
          targetColumns={rule.target_columns}
          highlightScope={rule.highlight_scope}
          colNames={colNames}
          tableFields={tableFields}
          onUpdateConditions={(newConditions: ExcelConditionItem[]) =>
            onUpdateRule({
              conditions: newConditions,
            })
          }
          onUpdateTargetColumns={(newTargetColumns: string[]) =>
            onUpdateRule({
              target_columns: newTargetColumns,
              column_name: newTargetColumns[0] || "",
            })
          }
          onUpdateHighlightScope={(newScope: string) =>
            onUpdateRule({
              highlight_scope: newScope,
            })
          }
        />
      )}

      {typeNorm === "cellis" && (
        <Grid
          templateColumns={
            ["between", "notBetween"].includes(rule.operator ?? "")
              ? "1fr 2fr"
              : "1fr 1fr"
          }
          gap={2}
          alignItems="end"
        >
          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Operator
            </Field.Label>
            <NativeSelect.Root size="xs">
              <NativeSelect.Field
                bg="white"
                h="28px"
                value={rule.operator ?? "equal"}
                onChange={(e) => onUpdateRule({ operator: e.target.value })}
              >
                {isOperatorAllowed("equal", colDataType) && (
                  <option value="equal">Equal to</option>
                )}
                {isOperatorAllowed("notEqual", colDataType) && (
                  <option value="notEqual">Not equal to</option>
                )}
                {isOperatorAllowed("greaterThan", colDataType) && (
                  <option value="greaterThan">Greater than</option>
                )}
                {isOperatorAllowed("lessThan", colDataType) && (
                  <option value="lessThan">Less than</option>
                )}
                {isOperatorAllowed("greaterThanOrEqual", colDataType) && (
                  <option value="greaterThanOrEqual">
                    Greater than or equal to
                  </option>
                )}
                {isOperatorAllowed("lessThanOrEqual", colDataType) && (
                  <option value="lessThanOrEqual">Less than or equal to</option>
                )}
                {isOperatorAllowed("between", colDataType) && (
                  <option value="between">Between</option>
                )}
                {isOperatorAllowed("notBetween", colDataType) && (
                  <option value="notBetween">Not between</option>
                )}
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
          </Field.Root>

          {["between", "notBetween"].includes(rule.operator ?? "") ? (
            <Grid
              templateColumns="1fr 1fr"
              gap={2}
              width="100%"
              alignItems="end"
            >
              <Field.Root gap={0}>
                <Field.Label
                  fontSize="xs"
                  fontWeight="semibold"
                  color="gray.600"
                  mb={0.5}
                >
                  Min Value
                </Field.Label>
                <Input
                  size="xs"
                  h="28px"
                  bg="white"
                  value={minVal}
                  onChange={(e) => {
                    const currentFormulas = Array.isArray(rule.formula)
                      ? [...rule.formula]
                      : [rule.formula ?? ""];
                    currentFormulas[0] = e.target.value;
                    onUpdateRule({ formula: currentFormulas });
                  }}
                  placeholder="Min"
                />
                {minValErr && (
                  <Text
                    color="red.500"
                    fontSize="9px"
                    mt={0.5}
                    lineHeight="normal"
                  >
                    {minValErr}
                  </Text>
                )}
              </Field.Root>
              <Field.Root gap={0}>
                <Field.Label
                  fontSize="xs"
                  fontWeight="semibold"
                  color="gray.600"
                  mb={0.5}
                >
                  Max Value
                </Field.Label>
                <Input
                  size="xs"
                  h="28px"
                  bg="white"
                  value={maxVal}
                  onChange={(e) => {
                    const currentFormulas = Array.isArray(rule.formula)
                      ? [...rule.formula]
                      : [rule.formula ?? ""];
                    if (currentFormulas.length === 0) {
                      currentFormulas[0] = "";
                    }
                    currentFormulas[1] = e.target.value;
                    onUpdateRule({ formula: currentFormulas });
                  }}
                  placeholder="Max"
                />
                {maxValErr && (
                  <Text
                    color="red.500"
                    fontSize="9px"
                    mt={0.5}
                    lineHeight="normal"
                  >
                    {maxValErr}
                  </Text>
                )}
              </Field.Root>
            </Grid>
          ) : (
            <Field.Root gap={0}>
              <Field.Label
                fontSize="xs"
                fontWeight="semibold"
                color="gray.600"
                mb={0.5}
              >
                Value / Formula
              </Field.Label>
              <Input
                size="xs"
                h="28px"
                bg="white"
                value={singleVal}
                onChange={(e) => {
                  const vals = e.target.value.split(",");
                  onUpdateRule({
                    formula: vals.length > 1 ? vals : e.target.value,
                  });
                }}
                placeholder="e.g. 10 or Keerthi Ramesh"
              />
              {singleValErr && (
                <Text
                  color="red.500"
                  fontSize="9px"
                  mt={0.5}
                  lineHeight="normal"
                >
                  {singleValErr}
                </Text>
              )}
            </Field.Root>
          )}
        </Grid>
      )}

      {(typeNorm === "containstext" ||
        typeNorm === "notcontainstext" ||
        typeNorm === "beginswith" ||
        typeNorm === "endswith") && (
        <Field.Root gap={0}>
          <Field.Label
            fontSize="xs"
            fontWeight="semibold"
            color="gray.600"
            mb={0.5}
          >
            Text Value
          </Field.Label>
          <Input
            size="xs"
            h="28px"
            bg="white"
            value={rule.text ?? ""}
            onChange={(e) => onUpdateRule({ text: e.target.value })}
            placeholder="Text to match"
          />
        </Field.Root>
      )}

      {typeNorm === "formula" && (
        <Grid templateColumns="1fr auto" gap={2} alignItems="end">
          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Formula Expression
            </Field.Label>
            <Input
              size="xs"
              h="28px"
              bg="white"
              value={
                Array.isArray(rule.formula)
                  ? (rule.formula[0] ?? "")
                  : (rule.formula ?? "")
              }
              onChange={(e) => onUpdateRule({ formula: [e.target.value] })}
              placeholder="e.g. =A2>100"
            />
            {formulaExprErr && (
              <Text color="red.500" fontSize="9px" mt={0.5} lineHeight="normal">
                {formulaExprErr}
              </Text>
            )}
          </Field.Root>
          <Flex h="28px" align="center">
            <Checkbox.Root
              size="xs"
              colorPalette="brand"
              checked={rule.stop_if_true ?? false}
              onCheckedChange={(details) =>
                onUpdateRule({ stop_if_true: !!details.checked })
              }
            >
              <Checkbox.HiddenInput />
              <Checkbox.Control />
              <Checkbox.Label fontSize="xs">Stop If True</Checkbox.Label>
            </Checkbox.Root>
          </Flex>
        </Grid>
      )}

      {typeNorm === "top10" && (
        <Grid templateColumns="1fr auto auto" gap={2} alignItems="end">
          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Rank / Items
            </Field.Label>
            <Input
              size="xs"
              h="28px"
              type="number"
              bg="white"
              value={rule.rank ?? 10}
              onChange={(e) =>
                onUpdateRule({ rank: parseInt(e.target.value) || 10 })
              }
            />
          </Field.Root>
          <Flex h="28px" align="center">
            <Checkbox.Root
              size="xs"
              colorPalette="brand"
              checked={rule.percent ?? false}
              onCheckedChange={(details) =>
                onUpdateRule({ percent: !!details.checked })
              }
            >
              <Checkbox.HiddenInput />
              <Checkbox.Control />
              <Checkbox.Label fontSize="xs">Percent</Checkbox.Label>
            </Checkbox.Root>
          </Flex>
          <Flex h="28px" align="center">
            <Checkbox.Root
              size="xs"
              colorPalette="brand"
              checked={rule.bottom ?? false}
              onCheckedChange={(details) =>
                onUpdateRule({ bottom: !!details.checked })
              }
            >
              <Checkbox.HiddenInput />
              <Checkbox.Control />
              <Checkbox.Label fontSize="xs">Bottom</Checkbox.Label>
            </Checkbox.Root>
          </Flex>
        </Grid>
      )}

      {typeNorm === "aboveaverage" && (
        <Grid templateColumns="1fr auto" gap={2} alignItems="end">
          <Field.Root gap={0}>
            <Field.Label
              fontSize="xs"
              fontWeight="semibold"
              color="gray.600"
              mb={0.5}
            >
              Condition
            </Field.Label>
            <NativeSelect.Root size="xs">
              <NativeSelect.Field
                bg="white"
                h="28px"
                value={rule.above_average === false ? "below" : "above"}
                onChange={(e) =>
                  onUpdateRule({
                    above_average: e.target.value === "above",
                    below_average: e.target.value === "below",
                  })
                }
              >
                <option value="above">Above Average</option>
                <option value="below">Below Average</option>
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
          </Field.Root>
          <Flex h="28px" align="center">
            <Checkbox.Root
              size="xs"
              colorPalette="brand"
              checked={rule.equal_average ?? false}
              onCheckedChange={(details) =>
                onUpdateRule({ equal_average: !!details.checked })
              }
            >
              <Checkbox.HiddenInput />
              <Checkbox.Control />
              <Checkbox.Label fontSize="xs">Equal Avg</Checkbox.Label>
            </Checkbox.Root>
          </Flex>
        </Grid>
      )}

      {typeNorm === "timeperiod" && (
        <Field.Root gap={0}>
          <Field.Label
            fontSize="xs"
            fontWeight="semibold"
            color="gray.600"
            mb={0.5}
          >
            Time Period
          </Field.Label>
          <NativeSelect.Root size="xs">
            <NativeSelect.Field
              bg="white"
              h="28px"
              value={rule.time_period ?? "today"}
              onChange={(e) => onUpdateRule({ time_period: e.target.value })}
            >
              <option value="today">Today</option>
              <option value="yesterday">Yesterday</option>
              <option value="tomorrow">Tomorrow</option>
              <option value="last7Days">Last 7 Days</option>
              <option value="thisWeek">This Week</option>
              <option value="lastWeek">Last Week</option>
              <option value="thisMonth">This Month</option>
              <option value="lastMonth">Last Month</option>
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>
      )}

      {typeNorm === "colorscale" && (
        <Grid templateColumns="repeat(3, 1fr)" gap={2}>
          <Field.Root gap={0}>
            <Field.Label fontSize="xs" color="gray.600" mb={1}>
              Min Color
            </Field.Label>
            <HStack gap={1}>
              <ColorPicker
                value={rule.colors?.[0]}
                onChange={(hex: string) => {
                  const list = [
                    hex,
                    rule.colors?.[1] || "FFEB84",
                    rule.colors?.[2] || "63BE7B",
                  ];
                  onUpdateRule({ colors: list });
                }}
                w="14px"
                h="14px"
                borderRadius="full"
              />
              <Input
                size="xs"
                bg="white"
                value={rule.colors?.[0] ?? ""}
                onChange={(e) => {
                  const list = [
                    e.target.value.replace("#", ""),
                    rule.colors?.[1] || "FFEB84",
                    rule.colors?.[2] || "63BE7B",
                  ];
                  onUpdateRule({ colors: list });
                }}
                placeholder="F8696B"
              />
            </HStack>
          </Field.Root>
          <Field.Root gap={0}>
            <Field.Label fontSize="xs" color="gray.600" mb={1}>
              Mid Color
            </Field.Label>
            <HStack gap={1}>
              <ColorPicker
                value={rule.colors?.[1]}
                onChange={(hex: string) => {
                  const list = [
                    rule.colors?.[0] || "F8696B",
                    hex,
                    rule.colors?.[2] || "63BE7B",
                  ];
                  onUpdateRule({ colors: list });
                }}
                w="14px"
                h="14px"
                borderRadius="full"
              />
              <Input
                size="xs"
                bg="white"
                value={rule.colors?.[1] ?? ""}
                onChange={(e) => {
                  const list = [
                    rule.colors?.[0] || "F8696B",
                    e.target.value.replace("#", ""),
                    rule.colors?.[2] || "63BE7B",
                  ];
                  onUpdateRule({ colors: list });
                }}
                placeholder="FFEB84"
              />
            </HStack>
          </Field.Root>
          <Field.Root gap={0}>
            <Field.Label fontSize="xs" color="gray.600" mb={1}>
              Max Color
            </Field.Label>
            <HStack gap={1}>
              <ColorPicker
                value={rule.colors?.[2]}
                onChange={(hex: string) => {
                  const list = [
                    rule.colors?.[0] || "F8696B",
                    rule.colors?.[1] || "FFEB84",
                    hex,
                  ];
                  onUpdateRule({ colors: list });
                }}
                w="14px"
                h="14px"
                borderRadius="full"
              />
              <Input
                size="xs"
                bg="white"
                value={rule.colors?.[2] ?? ""}
                onChange={(e) => {
                  const list = [
                    rule.colors?.[0] || "F8696B",
                    rule.colors?.[1] || "FFEB84",
                    e.target.value.replace("#", ""),
                  ];
                  onUpdateRule({ colors: list });
                }}
                placeholder="63BE7B"
              />
            </HStack>
          </Field.Root>
        </Grid>
      )}

      {typeNorm === "databar" && (
        <VStack align="stretch" gap={3}>
          <Grid templateColumns="repeat(2, 1fr)" gap={2} alignItems="end">
            <Field.Root gap={0}>
              <Field.Label fontSize="xs" color="gray.600" mb={1}>
                Bar Color
              </Field.Label>
              <HStack gap={1}>
                <ColorPicker
                  value={rule.color}
                  onChange={(hex: string) => onUpdateRule({ color: hex })}
                  w="14px"
                  h="14px"
                  borderRadius="full"
                />
                <Input
                  size="xs"
                  bg="white"
                  value={rule.color ?? ""}
                  onChange={(e) =>
                    onUpdateRule({
                      color: e.target.value.replace("#", ""),
                    })
                  }
                  placeholder="638EC6"
                />
              </HStack>
              <Text fontSize="10px" color="gray.500" mt={1.5}>
                Note: Rendered as a gradient fill.
              </Text>
            </Field.Root>
            <Flex h="24px" align="center">
              <Checkbox.Root
                size="xs"
                colorPalette="brand"
                checked={rule.show_value ?? true}
                onCheckedChange={(details) =>
                  onUpdateRule({ show_value: !!details.checked })
                }
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label fontSize="xs">Show Value</Checkbox.Label>
              </Checkbox.Root>
            </Flex>
          </Grid>

          {/* Data Bar Min/Max bounds customization */}
          <Box borderTop="1px solid" borderColor="gray.200" pt={2} mt={1}>
            <Text fontSize="xs" fontWeight="semibold" color="gray.600" mb={2}>
              Configure Bounds
            </Text>
            <Grid templateColumns="1fr 1fr" gap={3}>
              <VStack align="stretch" gap={1.5}>
                <Text fontSize="10px" fontWeight="medium" color="gray.500">
                  Minimum
                </Text>
                <NativeSelect.Root size="xs">
                  <NativeSelect.Field
                    bg="white"
                    value={rule.start_type ?? "min"}
                    onChange={(e) =>
                      onUpdateRule({
                        start_type: e.target.value,
                        start_value:
                          e.target.value === "min"
                            ? undefined
                            : (rule.start_value ?? 0),
                      })
                    }
                  >
                    <option value="min">Automatic (Min)</option>
                    <option value="num">Number</option>
                    <option value="percent">Percent</option>
                    <option value="percentile">Percentile</option>
                    <option value="formula">Formula</option>
                  </NativeSelect.Field>
                  <NativeSelect.Indicator />
                </NativeSelect.Root>
                {rule.start_type && rule.start_type !== "min" && (
                  <Input
                    size="xs"
                    bg="white"
                    value={rule.start_value ?? ""}
                    onChange={(e) =>
                      onUpdateRule({ start_value: e.target.value })
                    }
                    placeholder="Value"
                  />
                )}
              </VStack>

              <VStack align="stretch" gap={1.5}>
                <Text fontSize="10px" fontWeight="medium" color="gray.500">
                  Maximum
                </Text>
                <NativeSelect.Root size="xs">
                  <NativeSelect.Field
                    bg="white"
                    value={rule.end_type ?? "max"}
                    onChange={(e) =>
                      onUpdateRule({
                        end_type: e.target.value,
                        end_value:
                          e.target.value === "max"
                            ? undefined
                            : (rule.end_value ?? 100),
                      })
                    }
                  >
                    <option value="max">Automatic (Max)</option>
                    <option value="num">Number</option>
                    <option value="percent">Percent</option>
                    <option value="percentile">Percentile</option>
                    <option value="formula">Formula</option>
                  </NativeSelect.Field>
                  <NativeSelect.Indicator />
                </NativeSelect.Root>
                {rule.end_type && rule.end_type !== "max" && (
                  <Input
                    size="xs"
                    bg="white"
                    value={rule.end_value ?? ""}
                    onChange={(e) =>
                      onUpdateRule({ end_value: e.target.value })
                    }
                    placeholder="Value"
                  />
                )}
              </VStack>
            </Grid>
          </Box>
        </VStack>
      )}

      {typeNorm === "iconset" && (
        <VStack align="stretch" gap={3}>
          <Grid templateColumns="repeat(2, 1fr)" gap={2} alignItems="end">
            <Field.Root gap={0}>
              <Field.Label fontSize="xs" color="gray.600" mb={1}>
                Icon Style
              </Field.Label>
              <NativeSelect.Root size="xs">
                <NativeSelect.Field
                  bg="white"
                  value={rule.icon_style ?? "3TrafficLights1"}
                  onChange={(e) => {
                    const style = e.target.value;
                    let defaultVals = [0, 33, 67];
                    if (style === "4Arrows") {
                      defaultVals = [0, 25, 50, 75];
                    } else if (style === "5Arrows") {
                      defaultVals = [0, 20, 40, 60, 80];
                    }
                    onUpdateRule({
                      icon_style: style,
                      values: defaultVals,
                    });
                  }}
                >
                  <option value="3TrafficLights1">Traffic Lights</option>
                  <option value="3Arrows">3 Arrows</option>
                  <option value="3Flags">3 Flags</option>
                  <option value="3Symbols">3 Symbols</option>
                  <option value="4Arrows">4 Arrows</option>
                  <option value="5Arrows">5 Arrows</option>
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
              {getIconSetPreview(rule.icon_style ?? "3TrafficLights1")}
            </Field.Root>
            <Flex h="24px" align="center">
              <Checkbox.Root
                size="xs"
                colorPalette="brand"
                checked={rule.show_value ?? true}
                onCheckedChange={(details) =>
                  onUpdateRule({ show_value: !!details.checked })
                }
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label fontSize="xs">Show Value</Checkbox.Label>
              </Checkbox.Root>
            </Flex>
          </Grid>

          {/* Icon Set Value Thresholds customization */}
          <Box borderTop="1px solid" borderColor="gray.200" pt={2} mt={1}>
            <Flex align="center" justify="space-between" mb={2}>
              <Text fontSize="xs" fontWeight="semibold" color="gray.600">
                Configure Thresholds
              </Text>
              <HStack gap={1}>
                <Text fontSize="10px" color="gray.500">
                  Type:
                </Text>
                <NativeSelect.Root size="xs" width="80px">
                  <NativeSelect.Field
                    bg="white"
                    value={rule.value_type ?? "percent"}
                    onChange={(e) =>
                      onUpdateRule({
                        value_type: e.target.value,
                        percent: e.target.value === "percent",
                      })
                    }
                  >
                    <option value="percent">Percent</option>
                    <option value="num">Number</option>
                  </NativeSelect.Field>
                  <NativeSelect.Indicator />
                </NativeSelect.Root>
              </HStack>
            </Flex>

            <VStack align="stretch" gap={2}>
              {(() => {
                const style = rule.icon_style ?? "3TrafficLights1";
                const is5 = style === "5Arrows";
                const is4 = style === "4Arrows";
                const numThresholds = is5 ? 4 : is4 ? 3 : 2;
                const currentValues =
                  rule.values ||
                  (is5
                    ? [0, 20, 40, 60, 80]
                    : is4
                      ? [0, 25, 50, 75]
                      : [0, 33, 67]);

                const labels = is5
                  ? [
                      "Tier 5 (Highest) Lower Bound",
                      "Tier 4 Lower Bound",
                      "Tier 3 Lower Bound",
                      "Tier 2 Lower Bound",
                    ]
                  : is4
                    ? [
                        "Tier 4 (Highest) Lower Bound",
                        "Tier 3 Lower Bound",
                        "Tier 2 Lower Bound",
                      ]
                    : ["Tier 3 (Highest) Lower Bound", "Tier 2 Lower Bound"];

                return Array.from({
                  length: numThresholds,
                }).map((_, tIdx) => {
                  const valIndex = numThresholds - tIdx;
                  const val =
                    currentValues[valIndex] ??
                    (is5
                      ? 80 - tIdx * 20
                      : is4
                        ? 75 - tIdx * 25
                        : 67 - tIdx * 34);

                  return (
                    <Flex key={tIdx} align="center" justify="space-between">
                      <Text fontSize="10px" color="gray.600">
                        {labels[tIdx]}
                      </Text>
                      <HStack gap={1} width="100px" justify="flex-end">
                        <Text fontSize="10px" color="gray.400">
                          &gt;=
                        </Text>
                        <Input
                          size="xs"
                          bg="white"
                          type="number"
                          width="60px"
                          value={val}
                          onChange={(e) => {
                            const newVal = parseFloat(e.target.value) || 0;
                            const newValues = [...currentValues];
                            newValues[valIndex] = newVal;
                            onUpdateRule({ values: newValues });
                          }}
                        />
                      </HStack>
                    </Flex>
                  );
                });
              })()}
            </VStack>
          </Box>
        </VStack>
      )}

      {/* Fallback empty message for duplicaterecords / uniquerecords */}
      {["duplicaterecords", "uniquerecords"].includes(typeNorm) && (
        <Flex h="28px" align="center">
          <Text fontSize="xs" color="gray.500" fontStyle="italic">
            Highlights all duplicate or unique values automatically.
          </Text>
        </Flex>
      )}
    </Box>
  );
}
