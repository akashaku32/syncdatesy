import { useState } from "react";

import { Box, Button, Flex, Text } from "@chakra-ui/react";

import { FiDownload } from "react-icons/fi";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Label,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import LoadingSpinner from "@/components/shared/Spinner";
import ServerRoutes from "@/constants/server-routes";
import useAuth from "@/context/Auth/useAuth";
import AxiosInstance from "@/lib/axios/api-client";
import useFetchBillingUsage from "@/queryOptions/billing/useFetchBillingUsage";
import Table, { type Column } from "@/shared/Table";
import {
  type BillingDataMap,
  type BillingDetail,
  type InvoiceItem,
} from "@/types/billing";

import BillingSelector from "./BillingSelector";
import { Chart, useChart } from "@chakra-ui/charts";

const BillingInfoTab = () => {
  const {
    authState: { user },
  } = useAuth();
  const [selectedRange, setSelectedRange] = useState<string[]>([
    "current-month",
  ]);
  const [detailsTab, setDetailsTab] = useState<"billing_details" | "invoices">(
    "billing_details",
  );

  const { data: BillingUsageData, isLoading: isLoadingUsage } =
    useFetchBillingUsage({
      companyId: user?.company.cmp_id as number,
      billingPeriod:
        selectedRange[0] === "last-year" ? "last-12-months" : undefined,
      enabled: true,
    });

  const billingDataMap = BillingUsageData as BillingDataMap;

  const monthlyLabels =
    billingDataMap?.daily_labels ??
    billingDataMap?.current_month_labels ??
    billingDataMap?.labels ??
    [];
  const monthlyValues =
    billingDataMap?.total_rec ??
    billingDataMap?.current_month_billing ??
    billingDataMap?.data ??
    [];
  const billingDataMonthly = monthlyLabels.map(
    (label: string, index: number) => ({
      day: label,
      usage: monthlyValues[index] ?? 0,
    }),
  );
  const billingDetails = billingDataMap?.billing_details ?? [];
  const invoices = billingDataMap?.invoices ?? [];

  const annualLabels =
    billingDataMap?.daily_labels ??
    billingDataMap?.monthly_labels ??
    billingDataMap?.labels ??
    [];
  const annualValues =
    billingDataMap?.total_rec ??
    billingDataMap?.monthly_total_rec_values ??
    billingDataMap?.data ??
    [];
  const billingDataAnnual = annualLabels.map(
    (label: string, index: number) => ({
      day: label,
      usage: annualValues[index] ?? 0,
    }),
  );

  const billingDetailColumns: Column<BillingDetail>[] = [
    {
      header: "Period",
      accessor: "period",
      width: "50%",
      textAlign: "left",
    },
    {
      header: "Total Usage",
      accessor: "total_usage",
      width: "50%",
      textAlign: "center",
      render: (value) =>
        `${Number(value || 0).toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })} Units`,
    },
  ];

  const formatDate = (value: unknown) => {
    if (!value) return "-";
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleDateString();
  };

  const formatCurrency = (value: unknown) => {
    if (value === undefined || value === null || value === "") return "$0.00";
    const num = Number(value);
    if (Number.isNaN(num)) return "$0.00";
    return `$${num.toFixed(2)}`;
  };

  const handleDownload = async (id: number) => {
    const { data, headers } = await AxiosInstance.get(
      ServerRoutes.billing.downloadInvoice({ id }),
      { responseType: "blob" },
    );

    const contentDisposition = String(headers?.["content-disposition"] || "");
    const fileNameMatch = /filename="?([^"]+)"?/.exec(contentDisposition);
    const fileName = fileNameMatch?.[1] || `invoice-${id}.pdf`;

    const url = window.URL.createObjectURL(new Blob([data]));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  const invoiceColumns: Column<InvoiceItem>[] = [
    {
      header: "Invoice Number",
      accessor: "invoice_number",
      width: "12%",
      textAlign: "left",
    },
    {
      header: "Invoice Date",
      accessor: "created_at",
      width: "10%",
      textAlign: "left",
      render: (value) => formatDate(value),
    },
    {
      header: "Start Date",
      accessor: "billing_start_date",
      width: "10%",
      textAlign: "left",
      render: (value) => formatDate(value),
    },
    {
      header: "End Date",
      accessor: "billing_end_date",
      width: "10%",
      textAlign: "left",
      render: (value) => formatDate(value),
    },
    {
      header: "Amount",
      accessor: "total_amount",
      width: "10%",
      textAlign: "left",
      render: (value) => formatCurrency(value),
    },
    {
      header: "Payment Status",
      accessor: "payment_status",
      width: "10%",
      textAlign: "left",
      render: (_, row: InvoiceItem) => {
        const status = String(row.payment_status || "-").toLowerCase();
        return (
          <Text fontSize="sm" fontWeight="normal" textTransform="lowercase">
            {status}
          </Text>
        );
      },
    },
    {
      header: "Reference No",
      accessor: "payment_reference",
      width: "20%",
      textAlign: "left",
      render: (value) => (
        <Text fontSize="sm" lineClamp={1}>
          {String(value || "-")}
        </Text>
      ),
    },
    {
      header: "Payment Date",
      accessor: "payment_date",
      width: "10%",
      textAlign: "left",
      render: (value) => <Text fontSize="sm">{formatDate(value)}</Text>,
    },
    {
      header: "Actions",
      accessor: "actions",
      width: "12%",
      textAlign: "center",
      render: (_, row: InvoiceItem) => (
        <Flex alignItems="center" justifyContent="center" gap={2}>
          <Button
            size="xs"
            variant="ghost"
            bg="transparent"
            borderWidth="0"
            _hover={{ bg: "transparent" }}
            _active={{ bg: "transparent" }}
            borderRadius="md"
            minW="24px"
            h="24px"
            p={0}
            onClick={() => row.id && handleDownload(row.id)}
            disabled={!row.id}
            aria-label="Download invoice"
            title="Download"
          >
            <FiDownload size={18} color="gray.600" />
          </Button>
        </Flex>
      ),
    },
  ];

  const chart = useChart({
    data:
      selectedRange[0] === "current-month"
        ? billingDataMonthly || []
        : billingDataAnnual || [],
    series: [{ name: "usage", color: "purple.300" }],
  });
  const billingXAxisLabel = selectedRange[0] === "last-year" ? "Month" : "Date";

  if (isLoadingUsage) return <LoadingSpinner />;

  return (
    <>
      <Flex justifyContent="flex-end" w="100%">
        <BillingSelector
          selectedRange={selectedRange}
          setSelectedRange={setSelectedRange}
        />
      </Flex>
      <Chart.Root maxH="sm" chart={chart} w="100%">
        <BarChart data={chart.data} margin={{ left: 20 }}>
          <CartesianGrid
            stroke={chart.color("border.muted") || "#e2e8f0"}
            vertical={false}
          />
          <XAxis
            axisLine={false}
            tickLine={false}
            dataKey="day"
            tickFormatter={(value) =>
              typeof value === "string" ? value.slice(0, 3) : value
            }
            label={{
              value: billingXAxisLabel,
              position: "insideBottom",
              offset: -5,
            }}
          />
          <YAxis width={60} axisLine={false} tickLine={false}>
            <Label
              value="Total Payment (USD)"
              angle={-90}
              position="insideLeft"
            />
          </YAxis>
          <Tooltip
            cursor={{ fill: chart.color("bg.muted") || "rgba(0,0,0,0.04)" }}
            animationDuration={0}
            content={<Chart.Tooltip />}
            labelFormatter={() => ""}
            formatter={(value: number) => [
              `${Number(value).toLocaleString()}`,
              "Total Payment (USD)",
            ]}
          />
          {chart.series.map((item) => (
            <Bar
              isAnimationActive={true}
              key={item.name}
              dataKey={item.name}
              fill={chart.color(item.color)}
            />
          ))}
        </BarChart>
      </Chart.Root>

      <Flex direction="column" gap={0} mt={-6} w="100%">
        <Text fontSize="lg" fontWeight="extrabold" color="gray.800">
          Billing information
        </Text>
        <Flex
          gap={10}
          borderBottom="1px solid"
          borderColor="gray.200"
          w="100%"
          justifyContent="center"
          position="relative"
          mb={4}
        >
          <Box
            as="button"
            fontSize="sm"
            fontWeight="600"
            color={detailsTab === "invoices" ? "purple.600" : "gray.500"}
            borderBottom="3px solid"
            borderColor={
              detailsTab === "invoices" ? "purple.600" : "transparent"
            }
            mb="-1px"
            pb={3}
            px={4}
            position="relative"
            zIndex={detailsTab === "invoices" ? 1 : 0}
            transition="all 0.2s"
            onClick={() => setDetailsTab("invoices")}
          >
            Invoice
          </Box>
          <Box
            as="button"
            fontSize="sm"
            fontWeight="600"
            color={detailsTab === "billing_details" ? "purple.600" : "gray.500"}
            borderBottom="3px solid"
            borderColor={
              detailsTab === "billing_details" ? "purple.600" : "transparent"
            }
            mb="-1px"
            pb={3}
            px={4}
            position="relative"
            zIndex={detailsTab === "billing_details" ? 1 : 0}
            transition="all 0.2s"
            onClick={() => setDetailsTab("billing_details")}
          >
            Billing details
          </Box>
        </Flex>

        <Box mt={1}>
          {detailsTab === "billing_details" ? (
            billingDetails.length === 0 ? (
              <Box
                borderWidth="1px"
                borderColor="gray.200"
                borderRadius="md"
                p={4}
                color="gray.500"
                fontSize="sm"
                w="100%"
                textAlign="center"
              >
                No billing details available.
              </Box>
            ) : (
              <Table
                data={billingDetails}
                columns={billingDetailColumns}
                pageSize={10}
                totalElements={billingDetails.length}
                updateCurrentPage={() => {}}
                hidePagination
              />
            )
          ) : invoices.length === 0 ? (
            <Box
              borderWidth="1px"
              borderColor="gray.200"
              borderRadius="md"
              p={8}
              color="gray.500"
              fontSize="sm"
              w="100%"
              textAlign="center"
            >
              No invoices available.
            </Box>
          ) : (
            <Table
              data={invoices}
              columns={invoiceColumns}
              pageSize={10}
              totalElements={invoices.length}
              updateCurrentPage={() => {}}
              hidePagination
            />
          )}
        </Box>
      </Flex>
    </>
  );
};

export default BillingInfoTab;
