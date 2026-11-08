import { Button, Flex, Text } from "@chakra-ui/react";

import { MdOutlineEdit } from "react-icons/md";

import { format } from "date-fns";
import { useNavigate, useOutletContext } from "react-router";

import ClientRoutes from "@/constants/client-routes";
import { dateTimeFormat } from "@/constants/common";
import usePermissions from "@/hooks/usePermissions";
import { type Connector } from "@/types/connectors";

import Form from "./Form";

type AuditUser = NonNullable<Connector["modified_by"]>;

const getFirstName = (user?: AuditUser | null) => {
  if (!user) return "";
  if (typeof user === "string") return user.trim().split(/\s+/)[0] || "";
  return user.first_name || "";
};

const getCreatedByName = (connector: Connector) =>
  getFirstName(connector.created_by) ||
  getFirstName(connector.created_by_name) ||
  "";

const formatDateTime = (date?: string | number | null) => {
  if (!date || date === "None") return "--";
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return "--";
  return format(parsed, dateTimeFormat);
};

const Settings = () => {
  const connector = useOutletContext<Connector>();
  const navigate = useNavigate();
  const { can } = usePermissions();

  const handleEditConnectorClick = () => {
    navigate(
      `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT_CONFIGURATION}/${
        connector.connection_id
      }`,
    );
  };

  const canEdit = can("can_edit_connectors");

  return (
    <Flex flexDirection="column" gap={4} w="100%">
      <Text fontSize="md" fontWeight="semibold">
        Connection details
      </Text>
      <Flex
        justifyContent={{ base: "space-between", md: "space-between" }}
        alignItems={{ base: "flex-start", md: "center" }}
        direction={{ base: "column", md: "row" }}
        gap={4}
      >
        <Flex gap={4} wrap="wrap">
          <Flex gap={1}>
            <Text fontSize="sm">Created by:</Text>
            <Text fontSize="sm" fontWeight="semibold">
              {getCreatedByName(connector) || "--"}
            </Text>
          </Flex>
          <Flex gap={1}>
            <Text fontSize="sm">Connected on:</Text>
            <Text fontSize="sm" fontWeight="semibold">
              {formatDateTime(connector?.connected_on)}
            </Text>
          </Flex>
        </Flex>
        {canEdit && (
          <Button
            variant="outline"
            colorPalette={"brand"}
            color={"brand.500"}
            onClick={handleEditConnectorClick}
          >
            <MdOutlineEdit color="brand.500" />
            Edit connection
          </Button>
        )}
      </Flex>
      <Form {...connector} />
    </Flex>
  );
};

export default Settings;
