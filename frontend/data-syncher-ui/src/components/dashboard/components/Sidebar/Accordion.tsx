import { Accordion, Flex, Text } from "@chakra-ui/react";

import { FaUsers } from "react-icons/fa6";
import { MdOutlineSettings } from "react-icons/md";

import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";
import usePermissions from "@/hooks/usePermissions";
import useFetchBillingUsage from "@/queryOptions/billing/useFetchBillingUsage";
import { Permissions } from "@/types/auth";

import MenuItem from "./MenuItem";

const SidebarAccordion = ({
  isActive,
  onMenuItemClick,
}: {
  isActive: (_path: string) => boolean;
  onMenuItemClick?: () => void;
}) => {
  const { can } = usePermissions();
  const {
    authState: { user },
  } = useAuth();
  const { refetch: refetchBillingUsage } = useFetchBillingUsage({
    companyId: user?.company.cmp_id as number,
    enabled: false,
  });

  const items: {
    value: string;
    title: string;
    icon: React.ReactNode;
    links: { label: string; path: string; permission?: keyof Permissions }[];
  }[] = [
    {
      value: "userSettings",
      title: "User Settings",
      icon: <FaUsers size={24} />,
      links: [
        {
          label: "Profile",
          path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.PROFILE}`,
        },
        {
          label: "Users",
          path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}`,
          permission: "can_view_users",
        },
      ],
    },
    {
      value: "accountSettings",
      title: "Account Settings",
      icon: <MdOutlineSettings size={24} />,
      links: [
        {
          label: "Profile",
          path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.PROFILE}`,
          permission: "can_access_settings",
        },
        {
          label: "Communications",
          path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.EMAIL}`,
          permission: "can_access_settings",
        },
        {
          label: "Billing and Usage",
          path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.BILLING}`,
          permission: "can_access_billing",
        },
        {
          label: "Agent Downloads",
          path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.AGENT_DOWNLOADS}`,
          permission: "can_access_settings",
        },
      ],
    },
  ];

  const filteredItems = items
    .map((item) => ({
      ...item,
      links: item.links.filter(
        (link) => !link.permission || can(link.permission),
      ),
    }))
    .filter((item) => item.links.length > 0);

  return (
    <Accordion.Root collapsible paddingInline={3} variant="plain">
      {filteredItems.map(({ title, links, value, icon }, index) => {
        return (
          <Accordion.Item key={index} value={value} mt={2}>
            <Accordion.ItemTrigger
              justifyContent="space-between"
              cursor="pointer"
            >
              <Flex gap={2} alignItems="center">
                {icon}
                <Text fontSize="lg">{title}</Text>
              </Flex>
              <Accordion.ItemIndicator />
            </Accordion.ItemTrigger>
            <Accordion.ItemContent ml={6}>
              <Accordion.ItemBody>
                {links.map(({ label, path }) => {
                  const active = isActive(path);
                  const shouldFetchBillingUsage = label === "Billing and Usage";
                  return (
                    <MenuItem
                      key={label}
                      label={label}
                      path={path}
                      onMenuItemClick={onMenuItemClick}
                      onClick={
                        shouldFetchBillingUsage
                          ? () => {
                              refetchBillingUsage();
                            }
                          : undefined
                      }
                      active={active}
                    />
                  );
                })}
              </Accordion.ItemBody>
            </Accordion.ItemContent>
          </Accordion.Item>
        );
      })}
    </Accordion.Root>
  );
};

export default SidebarAccordion;
