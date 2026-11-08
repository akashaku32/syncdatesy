import { startTransition } from "react";

import { Flex, Tabs } from "@chakra-ui/react";

import { useLocation, useNavigate, useParams } from "react-router";

import ClientRoutes from "@/constants/client-routes";
import usePermissions from "@/hooks/usePermissions";
import { Permissions } from "@/types/auth";
import { type Connector } from "@/types/connectors";

import {
  isSnowflakeToSnowflakeConnector,
  normalizeConnectorName,
} from "../../helpers";

interface ConnectorTabsProps {
  connector?: Connector;
}

const ConnectorTabs = ({ connector }: ConnectorTabsProps) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { connectionId } = useParams();
  const { can } = usePermissions();
  const normalizedDestinationName = normalizeConnectorName(
    connector?.destination_name,
  );
  const isFileExportDestination = ["sftp", "googledrive", "amazons3"].includes(
    normalizedDestinationName,
  );
  const shouldUseReverseSchema =
    (connector?.is_reverse_etl &&
      !isSnowflakeToSnowflakeConnector(connector)) ||
    isFileExportDestination;

  // Build the tab list with permissions
  const fullTabList: {
    label: string;
    route: string;
    permission?: keyof Permissions;
  }[] = [
    {
      label: "Overview",
      route: ClientRoutes.CONNECTORS.OVERVIEW,
      permission: "can_view_logs",
    },
    {
      label: "Schema",
      route: shouldUseReverseSchema
        ? ClientRoutes.CONNECTORS.REVERSE_SCHEMA
        : ClientRoutes.CONNECTORS.SCHEMA,
      permission: "can_view_tables",
    },
    {
      label: "Usage",
      route: ClientRoutes.CONNECTORS.USAGE,
      permission: "can_view_metrics",
    },
    {
      label: "Settings",
      route: ClientRoutes.CONNECTORS.SETTINGS,
    },
  ];

  const TabList = fullTabList.filter(
    (tab) => !tab.permission || can(tab.permission),
  );

  // Get current active tab from URL
  const currentPath = location.pathname.split("/").pop();
  const activeTabIndex = TabList.findIndex((tab) => tab.route === currentPath);
  const defaultIndex = activeTabIndex >= 0 ? activeTabIndex : 0;

  const handleTabChange = (index: number) => {
    const selectedTab = TabList[index];
    if (selectedTab && connectionId) {
      const newPath = `/dashboard/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/${connectionId}/${selectedTab.route}`;

      // Mark navigation as non-urgent
      startTransition(() => {
        navigate(newPath);
      });
    }
  };

  return (
    <Flex
      borderTop="1px solid"
      borderBottom="1px solid"
      borderColor="gray.200"
      bg="white"
      marginInline={-5}
    >
      <Tabs.Root
        value={defaultIndex.toString()}
        onValueChange={(details) => handleTabChange(Number(details.value))}
        variant="line"
        colorPalette="brand"
      >
        <Tabs.List px={6}>
          {TabList.map((tab, index) => (
            <Tabs.Trigger
              key={tab.route}
              value={index.toString()}
              py={4}
              px={{ base: 4, md: 6 }}
              fontWeight="medium"
              fontSize="sm"
              color="gray.600"
              _selected={{
                color: "brand.600",
                borderBottomColor: "brand.500",
                borderBottomWidth: "2px",
              }}
              _hover={{
                color: "brand.500",
              }}
              transition="all 0.2s"
            >
              {tab.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
    </Flex>
  );
};

export default ConnectorTabs;
