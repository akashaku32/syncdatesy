import { useState } from "react";

import { Flex } from "@chakra-ui/react";

import { format } from "date-fns";
import { useOutletContext } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import LoadingSpinner from "@/components/shared/Spinner";
import { VIEW_CONFIG } from "@/constants/view-config";
import useFetchConnectorUsageById from "@/queryOptions/connector/useFetchConnectorUsage";
import { type Connector } from "@/types/connectors";

import UsageSelector from "./UsageSelector";
import { Chart, useChart } from "@chakra-ui/charts";

const Usage = () => {
  const context = useOutletContext<Connector>();
  const [selectedRange, setSelectedRange] = useState<string[]>([
    "current-month",
  ]);
  const { data, isLoading } = useFetchConnectorUsageById(context.connection_id);

  const dailyUsageData = data?.daily_labels.map((label, index) => ({
    month: format(new Date(label), "dd"),
    "New Records": data.new_rec_values_daily[index],
    "Modified Records": data.mod_rec_values_daily[index],
    "Deleted Records": data.del_rec_values_daily[index],
  }));

  const monthlyUsageData = data?.monthly_labels.map((label, index) => ({
    month: format(new Date(label), "MMM yyyy"),
    "New Records": data.new_rec_values_monthly[index],
    "Modified Records": data.mod_rec_values_monthly[index],
    "Deleted Records": data.del_rec_values_monthly[index],
  }));

  const chart = useChart({
    data:
      selectedRange[0] === "current-month"
        ? dailyUsageData || []
        : monthlyUsageData || [],
    series: [
      { name: "New Records", color: "purple.300" },
      { name: "Modified Records", color: "orange.400" },
      { name: "Deleted Records", color: "red.500" },
    ],
  });

  if (isLoading) {
    return <LoadingSpinner />;
  }

  return (
    <Flex
      flexDirection="column"
      gap={VIEW_CONFIG.pageGap}
      minW="2xl"
      w="100%"
      flex="1 1 auto"
      minH="0"
    >
      <Flex justifyContent="flex-end" w="100%">
        <UsageSelector
          selectedRange={selectedRange}
          setSelectedRange={setSelectedRange}
        />
      </Flex>
      {/* container that fills remaining space at the bottom of the page */}
      <Flex flex="1 1 auto" minH="300px" minW="0" w="100%" overflowY="auto">
        <Chart.Root chart={chart} h="100%" w="100%">
          <BarChart data={chart.data}>
            <CartesianGrid
              stroke={chart.color("border.muted")}
              vertical={false}
            />
            <XAxis
              axisLine={false}
              tickLine={false}
              dataKey={chart.key("month")}
              tickFormatter={(value) => value.slice(0, 3)}
            />
            <YAxis tickLine={false} stroke={chart.color("border")} />
            <Tooltip
              cursor={{ fill: chart.color("bg.muted") }}
              animationDuration={100}
              content={<Chart.Tooltip />}
            />
            <Legend content={<Chart.Legend />} />
            {chart.series.map((item) => (
              <Bar
                isAnimationActive={true}
                key={item.name}
                dataKey={chart.key(item.name)}
                fill={chart.color(item.color)}
                stroke={chart.color(item.color)}
                stackId={item.stackId}
              >
                <LabelList
                  dataKey={chart.key(item.name)}
                  position="top"
                  style={{ fontWeight: "600", fill: chart.color("fg") }}
                />
              </Bar>
            ))}
          </BarChart>
        </Chart.Root>
      </Flex>
    </Flex>
  );
};

export default Usage;
