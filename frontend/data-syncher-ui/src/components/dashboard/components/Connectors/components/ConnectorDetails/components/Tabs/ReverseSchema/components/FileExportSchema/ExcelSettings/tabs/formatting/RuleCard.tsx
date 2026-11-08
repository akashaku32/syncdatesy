import {
  Box,
  Checkbox,
  Field,
  Flex,
  Grid,
  IconButton,
  NativeSelect,
  Text,
  VStack,
} from "@chakra-ui/react";

import { IoMdTrash } from "react-icons/io";

import { MultiColumnSelect } from "../../components/MultiColumnSelect";
import RuleStyleEditor from "../../components/RuleStyleEditor";
import {
  type RuleCardProps,
  getColumnDataType,
  isRuleTypeAllowed,
  normalizeRuleType,
} from "../../helpers";
import RuleTypeConfig from "./RuleTypeConfig";

export default function RuleCard({
  rule,
  index,
  colNames,
  tableFields,
  onUpdateRule,
  onUpdateRuleStyle,
  onDeleteRule,
}: RuleCardProps) {
  const ruleStyle = rule.style || {};
  const typeNorm = normalizeRuleType(rule.type);
  const evalCol = rule.condition_column_name || rule.column_name || "";
  const rawType = tableFields[evalCol] || "";
  const colDataType = getColumnDataType(rawType);

  const isStylingInternally = ["colorscale", "databar", "iconset"].includes(
    typeNorm,
  );

  const renderRuleTypeField = () => (
    <Field.Root gap={0}>
      <Field.Label
        fontSize="xs"
        fontWeight="semibold"
        color="gray.600"
        mb={0.5}
      >
        Rule Type
      </Field.Label>
      <NativeSelect.Root size="xs">
        <NativeSelect.Field
          bg="white"
          h="28px"
          value={typeNorm}
          onChange={(e) =>
            onUpdateRule(index, {
              type: e.target.value,
            })
          }
        >
          {isRuleTypeAllowed("cellis", colDataType) && (
            <option value="cellis">By Single Field</option>
          )}
          <option value="multipleconditions">By Multiple Fields</option>
          {isRuleTypeAllowed("containstext", colDataType) && (
            <option value="containstext">Contains Text</option>
          )}
          {isRuleTypeAllowed("notcontainstext", colDataType) && (
            <option value="notcontainstext">Does Not Contain Text</option>
          )}
          {isRuleTypeAllowed("beginswith", colDataType) && (
            <option value="beginswith">Begins With</option>
          )}
          {isRuleTypeAllowed("endswith", colDataType) && (
            <option value="endswith">Ends With</option>
          )}
          {isRuleTypeAllowed("duplicaterecords", colDataType) && (
            <option value="duplicaterecords">Duplicate Values</option>
          )}
          {isRuleTypeAllowed("uniquerecords", colDataType) && (
            <option value="uniquerecords">Unique Values</option>
          )}
          {isRuleTypeAllowed("aboveaverage", colDataType) && (
            <option value="aboveaverage">Above/Below Average</option>
          )}
          {isRuleTypeAllowed("top10", colDataType) && (
            <option value="top10">Top/Bottom 10</option>
          )}
          {isRuleTypeAllowed("formula", colDataType) && (
            <option value="formula">Custom Formula</option>
          )}
          {isRuleTypeAllowed("colorscale", colDataType) && (
            <option value="colorscale">Color Scale</option>
          )}
          {isRuleTypeAllowed("databar", colDataType) && (
            <option value="databar">Data Bar</option>
          )}
          {isRuleTypeAllowed("iconset", colDataType) && (
            <option value="iconset">Icon Set</option>
          )}
        </NativeSelect.Field>
        <NativeSelect.Indicator />
      </NativeSelect.Root>
    </Field.Root>
  );

  const selectedCols =
    rule.target_columns && rule.target_columns.length > 0
      ? rule.target_columns
      : rule.column_name
        ? rule.column_name
            .split(",")
            .map((s: string) => s.trim())
            .filter(Boolean)
        : [];

  return (
    <Box
      id={`excel-rule-${index}`}
      p={2}
      bg="white"
      border="1px solid"
      borderColor="gray.200"
      borderRadius="md"
      shadow="xs"
    >
      {/* Header Row */}
      <Flex
        justifyContent="space-between"
        alignItems="center"
        mb={1.5}
        pb={1}
        borderBottom="1px solid"
        borderColor="gray.100"
      >
        <Text fontSize="xs" fontWeight="bold" color="brand.600">
          Rule #{index + 1}
        </Text>
        <IconButton
          aria-label="Delete rule"
          size="xs"
          variant="ghost"
          colorPalette="red"
          onClick={(e) => onDeleteRule(e, index)}
        >
          <IoMdTrash />
        </IconButton>
      </Flex>

      <Grid
        templateColumns={{
          base: "1fr",
          md: "minmax(0, 1fr) minmax(0, 1fr)",
        }}
        gap={3}
      >
        {/* Left: Criteria */}
        <VStack align="stretch" gap={1.5} minW="0" w="100%">
          {typeNorm === "multipleconditions" ? (
            <Grid templateColumns="1fr" gap={2} alignItems="end">
              {renderRuleTypeField()}
            </Grid>
          ) : (
            <VStack align="stretch" gap={1}>
              {rule.condition_column_name ? (
                <>
                  <Grid templateColumns="1fr" gap={2} alignItems="end">
                    {renderRuleTypeField()}
                  </Grid>
                  <Grid
                    templateColumns="repeat(2, 1fr)"
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
                        Condition Column (Evaluate)
                      </Field.Label>
                      <NativeSelect.Root size="xs">
                        <NativeSelect.Field
                          bg="white"
                          h="28px"
                          value={rule.condition_column_name ?? ""}
                          onChange={(e) =>
                            onUpdateRule(index, {
                              condition_column_name:
                                e.target.value || undefined,
                            })
                          }
                        >
                          <option value="">
                            -- Select Condition Column --
                          </option>
                          {colNames.map((col: string) => (
                            <option key={col} value={col}>
                              {col}
                            </option>
                          ))}
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
                        Target Column(s) (Style)
                      </Field.Label>
                      <MultiColumnSelect
                        allColumns={colNames}
                        selectedColumns={selectedCols}
                        placeholder="Select Target Column(s)"
                        onChange={(newCols: string[]) =>
                          onUpdateRule(index, {
                            target_columns: newCols,
                            column_name: newCols.join(","),
                            range: undefined,
                          })
                        }
                      />
                    </Field.Root>
                  </Grid>
                </>
              ) : (
                <Grid templateColumns="repeat(2, 1fr)" gap={2} alignItems="end">
                  {renderRuleTypeField()}
                  <Field.Root gap={0}>
                    <Field.Label
                      fontSize="xs"
                      fontWeight="semibold"
                      color="gray.600"
                      mb={0.5}
                    >
                      Column Name
                    </Field.Label>
                    <NativeSelect.Root size="xs">
                      <NativeSelect.Field
                        bg="white"
                        h="28px"
                        value={rule.column_name ?? ""}
                        onChange={(e) =>
                          onUpdateRule(index, {
                            column_name: e.target.value,
                            target_columns: undefined,
                            range: undefined,
                          })
                        }
                      >
                        <option value="">-- Select Column --</option>
                        {colNames.map((col: string) => (
                          <option key={col} value={col}>
                            {col}
                          </option>
                        ))}
                      </NativeSelect.Field>
                      <NativeSelect.Indicator />
                    </NativeSelect.Root>
                  </Field.Root>
                </Grid>
              )}

              <Checkbox.Root
                size="xs"
                colorPalette="brand"
                checked={!!rule.condition_column_name}
                onCheckedChange={(details) => {
                  if (!details.checked) {
                    onUpdateRule(index, {
                      condition_column_name: undefined,
                    });
                  } else {
                    onUpdateRule(index, {
                      condition_column_name:
                        rule.column_name || colNames[0] || "",
                    });
                  }
                }}
                mt={0.5}
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label fontSize="10px" color="gray.600">
                  Evaluate condition based on a different column
                </Checkbox.Label>
              </Checkbox.Root>
            </VStack>
          )}

          {/* Specific rule parameters */}
          <RuleTypeConfig
            rule={rule}
            typeNorm={typeNorm}
            colDataType={colDataType}
            colNames={colNames}
            tableFields={tableFields}
            onUpdateRule={(patch) => onUpdateRule(index, patch)}
          />
        </VStack>

        {/* Right: Style / Format to Apply */}
        <Box minW="0" w="100%">
          {!isStylingInternally ? (
            <RuleStyleEditor
              rule={rule}
              ruleStyle={ruleStyle}
              onUpdateRule={(patch) => onUpdateRule(index, patch)}
              onUpdateRuleStyle={(patch) => onUpdateRuleStyle(index, patch)}
            />
          ) : (
            <Flex
              align="center"
              justify="center"
              h="100%"
              pl={5}
              borderLeft={{ base: "none", md: "1px solid" }}
              borderColor="gray.200"
            >
              <Text
                fontSize="xs"
                color="gray.400"
                fontStyle="italic"
                textAlign="center"
              >
                Formatting is managed internally by the selected visual scale.
              </Text>
            </Flex>
          )}
        </Box>
      </Grid>
    </Box>
  );
}
