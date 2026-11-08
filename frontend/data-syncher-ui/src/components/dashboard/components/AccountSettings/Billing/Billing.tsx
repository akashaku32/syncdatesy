import { useState } from "react";

import { Box, Flex } from "@chakra-ui/react";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import { VIEW_CONFIG } from "@/constants/view-config";

import BillingInfoTab from "./BillingInfoTab";
import UsageTab from "./UsageTab";

const Billing = () => {
  const [pageTab, setPageTab] = useState<"billing" | "usage">("billing");

  return (
    <Flex
      flexDirection="column"
      gap={VIEW_CONFIG.pageGap}
      minW="3xl"
      w="100%"
      alignItems="stretch"
    >
      <Flex justifyContent="space-between" alignItems="flex-start" w="100%">
        <PageHeader
          breadcrumbs={[
            {
              label: "Account Settings",
              route: "",
            },
          ]}
          title="Billing and Usage"
        />
      </Flex>
      <Box borderBottom="1px solid" borderColor="gray.100" mt={-4} />
      <Flex gap={8} mt={-8}>
        {[
          { id: "billing", label: "Billing" },
          { id: "usage", label: "Usage" },
        ].map((tab) => (
          <Box
            key={tab.id}
            as="button"
            fontSize="md"
            fontWeight={pageTab === tab.id ? "700" : "500"}
            color={pageTab === tab.id ? "purple.600" : "gray.600"}
            position="relative"
            onClick={() => setPageTab(tab.id as "billing" | "usage")}
          >
            {tab.label}
          </Box>
        ))}
      </Flex>

      {pageTab === "usage" ? <UsageTab /> : <BillingInfoTab />}
    </Flex>
  );
};

export default Billing;
