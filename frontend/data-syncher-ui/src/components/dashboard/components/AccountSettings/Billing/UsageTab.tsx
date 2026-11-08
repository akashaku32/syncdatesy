import { useEffect, useMemo, useState } from "react";

import { Box, Button, Checkbox, Flex, Image, Text } from "@chakra-ui/react";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Label,
  Legend,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import NoConnectionsIllustration from "@/components/dashboard/components/Connectors/assets/empty.svg";
import LoadingSpinner from "@/components/shared/Spinner";
import ServerRoutes from "@/constants/server-routes";
import useAuth from "@/context/Auth/useAuth";
import AxiosInstance from "@/lib/axios/api-client";
import useFetchAnnualBilling from "@/queryOptions/billing/useFetchAnnualBilling";
import useFetchMonthlyUsage from "@/queryOptions/billing/useFetchMonthlyUsage";
import Table from "@/shared/Table";
import { type MonthlyUsageResponse } from "@/types/billing";

import { Chart, useChart } from "@chakra-ui/charts";

const roundAxisTick = (value: number) => {
  if (value <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const roundedNormalized =
    normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return roundedNormalized * magnitude;
};

const applyConnectionFilter = (
  base: MonthlyUsageResponse | null,
  connectionIds: number[],
) => {
  if (!base) return null;

  const targetIds =
    connectionIds.length > 0
      ? connectionIds
      : (base.available_connections || []).map((c) => c.connection_id);

  const selectedSet = new Set(targetIds.map((id) => String(id)));
  const anyBase = base as unknown as Record<string, unknown>;

  const getSourceType = (id: number | string) => {
    return base.available_connections?.find(
      (c) => String(c.connection_id) === String(id),
    )?.source_type;
  };

  const candidatesRaw =
    (anyBase["connection_usage"] as unknown) ||
    (anyBase["connections_usage"] as unknown) ||
    (anyBase["connection_usage_data"] as unknown) ||
    (anyBase["connection_usage_map"] as unknown) ||
    (anyBase["connection_wise_usage"] as unknown);

  const sumArrays = (arrays: number[][]): number[] => {
    if (!Array.isArray(arrays) || arrays.length === 0) return [];
    const length = Math.max(...arrays.map((a) => (a && a.length) || 0));
    const totals = new Array<number>(length).fill(0);
    arrays.forEach((arr) => {
      if (!Array.isArray(arr)) return;
      for (let i = 0; i < length; i += 1) {
        totals[i] += (arr[i] as number) || 0;
      }
    });
    return totals;
  };

  if (Array.isArray(candidatesRaw)) {
    const candidates = candidatesRaw as {
      connection_id: number;
      total_rec: number[];
      daily_labels?: string[];
    }[];
    const selected = candidates.filter(
      (item) =>
        item &&
        typeof item === "object" &&
        selectedSet.has(String(item.connection_id)),
    );

    const filterBySource = (src: string) => {
      return selected
        .filter(
          (item) =>
            getSourceType(item.connection_id)?.toLowerCase() ===
            src.toLowerCase(),
        )
        .map((item) => item.total_rec)
        .filter((arr) => Array.isArray(arr));
    };

    return {
      ...base,
      total_rec: sumArrays(
        selected.map((item) => item.total_rec).filter(Array.isArray),
      ),
      amazon_s3_rec: sumArrays(filterBySource("amazons3")),
      salesforce_rec: sumArrays(filterBySource("salesforce")),
      dynamics_rec: sumArrays(filterBySource("microsoftdynamics365_fo")),
      google_reviews_rec: sumArrays(filterBySource("googlereviews")),
      selected_connection_ids: connectionIds,
      daily_labels:
        base.daily_labels ||
        (selected[0] as { daily_labels?: string[] })?.daily_labels ||
        [],
    } as MonthlyUsageResponse;
  }

  if (candidatesRaw && typeof candidatesRaw === "object") {
    const candidates = candidatesRaw as Record<
      string,
      { total_rec?: number[] }
    >;

    const getDataById = (id: number | string) => {
      const item =
        candidates[String(id)] ||
        (candidates as unknown as Record<number, { total_rec?: number[] }>)[
          Number(id)
        ];
      if (!item) return null;
      if (Array.isArray(item.total_rec)) return item.total_rec;
      if (Array.isArray(item)) return item;
      return null;
    };

    const filterBySource = (src: string) => {
      return targetIds
        .filter((id) => getSourceType(id)?.toLowerCase() === src.toLowerCase())
        .map((id) => getDataById(id))
        .filter((arr) => Array.isArray(arr));
    };

    const allArrays = targetIds
      .map((id) => getDataById(id))
      .filter((arr) => Array.isArray(arr));

    return {
      ...base,
      total_rec: sumArrays(allArrays as number[][]),
      amazon_s3_rec: sumArrays(filterBySource("amazons3")),
      salesforce_rec: sumArrays(filterBySource("salesforce")),
      dynamics_rec: sumArrays(filterBySource("microsoftdynamics365_fo")),
      google_reviews_rec: sumArrays(filterBySource("googlereviews")),
      selected_connection_ids: connectionIds,
    } as MonthlyUsageResponse;
  }

  return {
    ...base,
    selected_connection_ids: connectionIds,
  } as MonthlyUsageResponse;
};

const UsageTab = () => {
  const {
    authState: { user },
  } = useAuth();
  const [usageRange, setUsageRange] = useState<"monthly" | "annually">(
    "monthly",
  );

  const { data: AnnualBillingData, isLoading: isLoadingAnnualBilling } =
    useFetchAnnualBilling({
      companyId: user?.company.cmp_id as number,
      enabled: usageRange === "annually",
    });

  const { data: MonthlyUsageData, isLoading: isLoadingMonthlyUsage } =
    useFetchMonthlyUsage({
      companyId: user?.company.cmp_id as number,
      enabled: true,
    });

  const [selectedYear, setSelectedYear] = useState<number | null>(null);
  const [selectedMonth, setSelectedMonth] = useState<number | null>(null);
  const [selectedConnections, setSelectedConnections] = useState<number[]>([]);
  const [fullUsageData, setFullUsageData] =
    useState<MonthlyUsageResponse | null>(null);

  const usageData = useMemo(() => {
    if (!fullUsageData) return null;
    return applyConnectionFilter(fullUsageData, selectedConnections);
  }, [fullUsageData, selectedConnections]);

  useEffect(() => {
    if (!MonthlyUsageData) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFullUsageData(MonthlyUsageData);
    setSelectedYear(MonthlyUsageData.selected_year ?? null);
    setSelectedMonth(MonthlyUsageData.selected_month ?? null);
    setSelectedConnections(MonthlyUsageData.selected_connection_ids || []);
  }, [MonthlyUsageData]);

  const handleUsageUpdate = async (next: {
    year?: number;
    month?: number;
    connectionIds?: number[];
  }) => {
    if (!user?.company.cmp_id) return;
    const year = next.year ?? selectedYear ?? usageData?.selected_year;
    const month = next.month ?? selectedMonth ?? usageData?.selected_month;
    const connectionIds =
      next.connectionIds ??
      selectedConnections ??
      usageData?.selected_connection_ids ??
      [];

    if (!year || !month) return;

    await AxiosInstance.post(
      ServerRoutes.billing.updateConnectionsUsage({
        companyId: user.company.cmp_id,
      }),
      {
        selected_year: year,
        selected_month: month,
        connection_ids: connectionIds.join(","),
      },
    );

    const { data } = await AxiosInstance.get(
      ServerRoutes.billing.monthlyUsage({
        companyId: user.company.cmp_id,
      }),
      {
        params: {
          year,
          month,
          connection_ids: connectionIds,
        },
      },
    );

    const incoming = data as MonthlyUsageResponse;
    const incomingAny = incoming as Record<string, unknown>;
    const hasBreakdown =
      !!incomingAny?.connection_usage ||
      !!incomingAny?.connections_usage ||
      !!incomingAny?.connection_usage_data ||
      !!incomingAny?.connection_usage_map ||
      !!incomingAny?.connection_wise_usage;

    if (hasBreakdown || !fullUsageData) {
      setFullUsageData(incoming);
    }

    if (Array.isArray(incoming.selected_connection_ids)) {
      setSelectedConnections(incoming.selected_connection_ids);
    }

    setFullUsageData((prev) => {
      const keepConnections =
        !incoming.available_connections ||
        incoming.available_connections.length === 0;

      return {
        ...(prev || {}),
        ...incoming,
        available_connections: keepConnections
          ? prev?.available_connections || []
          : incoming.available_connections,
      } as MonthlyUsageResponse;
    });
  };

  const annualBillingMap = AnnualBillingData as {
    monthly_labels?: string[];
    monthly_total_rec_values?: number[];
  };

  const isSingleConnection =
    selectedConnections.length === 1 && usageRange === "monthly";
  const singleConnection = useMemo(() => {
    return isSingleConnection
      ? (
          fullUsageData?.available_connections as unknown as {
            connection_id: number;
            src_config__name: string;
            source_type: string;
          }[]
        )?.find((c) => c.connection_id === selectedConnections[0])
      : null;
  }, [
    isSingleConnection,
    fullUsageData?.available_connections,
    selectedConnections,
  ]);

  const singleConnectionName =
    singleConnection?.src_config__name || "Connection";
  const selectedSourceTypes = useMemo(() => {
    if (!selectedConnections.length) return new Set<string>();
    const selectedSet = new Set(selectedConnections);
    return new Set(
      (fullUsageData?.available_connections ?? [])
        .filter((connection) => selectedSet.has(connection.connection_id))
        .map((connection) => connection.source_type?.toLowerCase())
        .filter((sourceType): sourceType is string => !!sourceType),
    );
  }, [fullUsageData?.available_connections, selectedConnections]);
  const hasAmazonS3Source = selectedSourceTypes.has("amazons3");
  const hasSalesforceSource = selectedSourceTypes.has("salesforce");
  const hasDynamicsSource = selectedSourceTypes.has("microsoftdynamics365_fo");
  const hasGoogleReviewsSource = selectedSourceTypes.has("googlereviews");
  const monthlyOriginalUsageByDay = useMemo(() => {
    if (usageRange !== "monthly") return {};

    return (usageData?.daily_labels ?? []).reduce(
      (acc, label, index) => {
        acc[String(label)] = {
          AmazonS3: (usageData?.amazon_s3_rec ?? [])[index] ?? 0,
          Salesforce: (usageData?.salesforce_rec ?? [])[index] ?? 0,
          "Dynamics 365 FO": (usageData?.dynamics_rec ?? [])[index] ?? 0,
          "Google Reviews": (usageData?.google_reviews_rec ?? [])[index] ?? 0,
        };
        return acc;
      },
      {} as Record<string, Record<string, number>>,
    );
  }, [usageData, usageRange]);
  const monthlyScaleConfig = useMemo(() => {
    if (usageRange !== "monthly") {
      return {
        maxAxisValue: undefined as number | undefined,
        ticks: undefined as number[] | undefined,
      };
    }

    let maxStackTotal = 0;

    Object.values(monthlyOriginalUsageByDay).forEach((seriesValues) => {
      const values = Object.values(seriesValues);
      const dayTotal = values.reduce((sum, value) => sum + value, 0);
      maxStackTotal = Math.max(maxStackTotal, dayTotal);
    });

    if (maxStackTotal <= 0) {
      return {
        maxAxisValue: undefined,
        ticks: undefined,
      };
    }

    const maxAxisValue = roundAxisTick(maxStackTotal * 1.05);
    const order = Math.max(Math.floor(Math.log10(maxAxisValue)), 0);
    const lowTick = 5 * 10 ** Math.max(order - 3, 0);
    const middleTick = roundAxisTick(maxAxisValue / 2);
    const ticks = [0, lowTick, middleTick, maxAxisValue]
      .filter((tick) => tick <= maxAxisValue)
      .filter((tick, index, array) => array.indexOf(tick) === index)
      .sort((a, b) => a - b);

    return {
      maxAxisValue,
      ticks,
    };
  }, [monthlyOriginalUsageByDay, usageRange]);

  const usageChartData =
    usageRange === "annually"
      ? (annualBillingMap?.monthly_labels ?? []).map(
          (label: string, index: number) => ({
            day: label,
            "Total Tokens":
              (annualBillingMap?.monthly_total_rec_values ?? [])[index] ?? 0,
          }),
        )
      : (usageData?.daily_labels ?? []).map((label: string, index: number) => {
          const s3Usage = (usageData?.amazon_s3_rec ?? [])[index] ?? 0;
          const sfUsage = (usageData?.salesforce_rec ?? [])[index] ?? 0;
          const dyUsage = (usageData?.dynamics_rec ?? [])[index] ?? 0;
          const grUsage = (usageData?.google_reviews_rec ?? [])[index] ?? 0;

          return {
            day: label,
            ...(s3Usage > 0 ? { AmazonS3: s3Usage } : {}),
            ...(sfUsage > 0 ? { Salesforce: sfUsage } : {}),
            ...(dyUsage > 0 ? { "Dynamics 365 FO": dyUsage } : {}),
            ...(grUsage > 0 ? { "Google Reviews": grUsage } : {}),
          };
        });
  const usageXAxisLabel = usageRange === "annually" ? "Month" : "Date";

  const usageChart = useChart({
    data: (usageChartData || []) as Record<string, string | number>[],
    series:
      usageRange === "annually"
        ? [{ name: "Total Tokens", color: "purple.300" }]
        : [
            ...(hasAmazonS3Source
              ? [{ name: "AmazonS3", color: "red.200" }]
              : []),
            ...(hasSalesforceSource
              ? [{ name: "Salesforce", color: "#b0d6eeff" }]
              : []),
            ...(hasDynamicsSource
              ? [{ name: "Dynamics 365 FO", color: "#1c39bb" }]
              : []),
            ...(hasGoogleReviewsSource
              ? [{ name: "Google Reviews", color: "green.400" }]
              : []),
          ],
  });

  if (isLoadingMonthlyUsage || isLoadingAnnualBilling)
    return <LoadingSpinner />;

  return (
    <Flex direction="column" gap={4} mt={-12}>
      <Flex justifyContent="space-between" alignItems="flex-end" w="100%">
        <Flex alignItems="center" gap={2}>
          {usageRange === "monthly" && (
            <>
              <Box
                as="select"
                w="120px"
                h="32px"
                fontSize="sm"
                border="1px solid"
                borderColor="gray.300"
                borderRadius="md"
                px={2}
                {...({
                  value: selectedMonth ?? usageData?.selected_month ?? "",
                  onChange: (e: { target: { value: string } }) => {
                    const nextMonth = Number(e.target.value);
                    setSelectedMonth(nextMonth);
                  },
                } as Record<string, unknown>)}
              >
                {(usageData?.months ?? []).map(([value, name]) => (
                  <option key={value} value={value}>
                    {name}
                  </option>
                ))}
              </Box>
              <Box
                as="select"
                w="100px"
                h="32px"
                fontSize="sm"
                border="1px solid"
                borderColor="gray.300"
                borderRadius="md"
                px={2}
                {...({
                  value: selectedYear ?? usageData?.selected_year ?? "",
                  onChange: (e: { target: { value: string } }) => {
                    const nextYear = Number(e.target.value);
                    setSelectedYear(nextYear);
                  },
                } as Record<string, unknown>)}
              >
                {Array.from(new Set([...(usageData?.years ?? []), 2025]))
                  .sort((a, b) => b - a)
                  .map((year) => (
                    <option key={year} value={year}>
                      {year}
                    </option>
                  ))}
              </Box>
              <Button
                size="sm"
                variant="outline"
                colorPalette="purple"
                px={4}
                onClick={() => handleUsageUpdate({})}
              >
                Update
              </Button>
            </>
          )}
        </Flex>

        <Flex direction="column" alignItems="flex-end" gap={1}>
          <Box fontWeight="bold" fontSize="lg" color="gray.700">
            Connection Usage Information
          </Box>
          <Flex bg="transparent" p="0" width="fit-content" gap="2">
            {[
              { value: "monthly", label: "Monthly" },
              { value: "annually", label: "Annually" },
            ].map((option) => {
              const isSelected = usageRange === option.value;
              return (
                <Box
                  key={option.value}
                  as="button"
                  onClick={() =>
                    setUsageRange(option.value as "monthly" | "annually")
                  }
                  bg={isSelected ? "purple.600" : "white"}
                  color={isSelected ? "white" : "purple.600"}
                  border="1px solid"
                  borderColor="purple.600"
                  py={1.5}
                  px={4}
                  borderRadius="md"
                  fontSize="sm"
                  fontWeight="bold"
                  shadow="sm"
                  transition="all 0.2s"
                  _hover={{
                    bg: isSelected ? "purple.700" : "purple.50",
                  }}
                  cursor="pointer"
                >
                  {option.label}
                </Box>
              );
            })}
          </Flex>
        </Flex>
      </Flex>

      {usageRange === "monthly" && (
        <>
          {(usageData?.available_connections ?? []).length === 0 ? (
            <Flex
              direction="column"
              alignItems="center"
              justifyContent="center"
              borderWidth="1px"
              borderColor="gray.200"
              borderRadius="md"
              py={12}
              px={6}
              gap={3}
            >
              <Image
                src={NoConnectionsIllustration}
                alt="No connections"
                maxW="160px"
              />
              <Text fontSize="sm" color="gray.600">
                No connections available.
              </Text>
            </Flex>
          ) : (
            <Table
              data={usageData?.available_connections ?? []}
              columns={[
                {
                  header: "Connection",
                  accessor: "src_config__name",
                  width: "45%",
                },
                {
                  header: "Destination",
                  accessor: "dst_config__name",
                  width: "45%",
                },
                {
                  header: "Select",
                  accessor: "connection_id",
                  width: "10%",
                  textAlign: "center",
                  render: (value) => {
                    const id = Number(value);
                    const checked = selectedConnections.includes(id);
                    return (
                      <Checkbox.Root
                        colorPalette="purple"
                        variant="solid"
                        checked={checked}
                        onCheckedChange={(e) => {
                          const next = e.checked
                            ? [...selectedConnections, id]
                            : selectedConnections.filter((x) => x !== id);
                          setSelectedConnections(next);
                        }}
                      >
                        <Checkbox.HiddenInput />
                        <Checkbox.Control cursor="pointer" />
                      </Checkbox.Root>
                    );
                  },
                },
              ]}
              pageSize={10}
              totalElements={(usageData?.available_connections ?? []).length}
              updateCurrentPage={() => {}}
              hidePagination
            />
          )}

          <Chart.Root maxH="sm" chart={usageChart} w="100%">
            <BarChart data={usageChart.data} margin={{ left: 20 }}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis
                axisLine={false}
                tickLine={false}
                dataKey="day"
                tickFormatter={(value) =>
                  typeof value === "string" ? value.slice(0, 3) : value
                }
                label={{
                  value: usageXAxisLabel,
                  position: "insideBottom",
                  offset: -5,
                }}
              />
              <YAxis
                width={90}
                axisLine={false}
                tickLine={false}
                domain={
                  monthlyScaleConfig.maxAxisValue
                    ? [0, monthlyScaleConfig.maxAxisValue]
                    : [0, "auto"]
                }
                scale="sqrt"
                ticks={monthlyScaleConfig.ticks}
                tickFormatter={(value) =>
                  typeof value === "number"
                    ? value.toLocaleString()
                    : String(value)
                }
              >
                <Label value="Total Tokens" angle={-90} position="insideLeft" />
              </YAxis>
              <Tooltip
                cursor={{ fill: "rgba(0,0,0,0.04)" }}
                animationDuration={0}
                labelFormatter={() => ""}
                formatter={(value: number, name: string) => {
                  let mappedName = name;
                  if (isSingleConnection) {
                    const st = singleConnection?.source_type?.toLowerCase();
                    const targetName =
                      st === "amazons3"
                        ? "AmazonS3"
                        : st === "salesforce"
                          ? "Salesforce"
                          : st === "microsoftdynamics365_fo"
                            ? "Dynamics 365 FO"
                            : st === "googlereviews"
                              ? "Google Reviews"
                              : "";

                    if (name === targetName) {
                      mappedName = singleConnectionName;
                    }
                  }
                  return [value, mappedName];
                }}
              />
              <Legend verticalAlign="top" height={36} />
              {hasAmazonS3Source && (
                <Bar
                  stackId="a"
                  dataKey={usageChart.key("AmazonS3")}
                  name="AmazonS3"
                  fill="#feb2b2"
                />
              )}
              {hasSalesforceSource && (
                <Bar
                  stackId="a"
                  dataKey={usageChart.key("Salesforce")}
                  name="Salesforce"
                  fill="#b0d6eeff"
                />
              )}
              {hasDynamicsSource && (
                <Bar
                  stackId="a"
                  dataKey={usageChart.key("Dynamics 365 FO")}
                  name="Dynamics 365 FO"
                  fill="#1c39bb"
                />
              )}
              {hasGoogleReviewsSource && (
                <Bar
                  stackId="a"
                  dataKey={usageChart.key("Google Reviews")}
                  name="Google Reviews"
                  fill="#68d391"
                />
              )}
            </BarChart>
          </Chart.Root>
        </>
      )}

      {usageRange === "annually" && (
        <Chart.Root maxH="sm" chart={usageChart} w="100%">
          <BarChart data={usageChart.data} margin={{ left: 20 }}>
            <CartesianGrid stroke="#e2e8f0" vertical={false} />
            <XAxis
              axisLine={false}
              tickLine={false}
              dataKey="day"
              tickFormatter={(value) =>
                typeof value === "string" ? value.slice(0, 3) : value
              }
              label={{
                value: usageXAxisLabel,
                position: "insideBottom",
                offset: -5,
              }}
            />
            <YAxis width={90} axisLine={false} tickLine={false}>
              <Label value="Total Tokens" angle={-90} position="insideLeft" />
            </YAxis>
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              animationDuration={0}
              labelFormatter={() => ""}
            />
            <Legend verticalAlign="top" height={36} />
            <Bar
              dataKey={usageChart.key("Total Tokens")}
              name="Total Tokens"
              fill="#c7a6ff"
            />
          </BarChart>
        </Chart.Root>
      )}
    </Flex>
  );
};

export default UsageTab;
