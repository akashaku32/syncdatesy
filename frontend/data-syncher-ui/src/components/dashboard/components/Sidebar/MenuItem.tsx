import { JSX } from "react";

import { Flex, Text } from "@chakra-ui/react";

import { useNavigate } from "react-router";

const MenuItem = ({
  label,
  icon,
  path,
  onClick,
  onMenuItemClick,
  active,
}: {
  label: string;
  icon?: JSX.Element;
  path?: string;
  onClick?: () => void | Promise<void>;
  onMenuItemClick?: () => void;
  active: boolean;
}) => {
  const navigate = useNavigate();

  const handleClick = () => {
    void onClick?.();
    if (path) {
      navigate(path);
    }
    onMenuItemClick?.();
  };

  return (
    <Flex
      key={label}
      alignItems="center"
      paddingBlock={2}
      paddingInline={3}
      mx={2}
      borderRadius="lg"
      cursor="pointer"
      gap={2}
      position="relative"
      onClick={() => {
        void handleClick();
      }}
      color={active ? "sidebar.activeFg" : "sidebar.fg"}
      bg={active ? "sidebar.activeBg" : "transparent"}
      borderLeftWidth={active ? "3px" : "3px"}
      borderLeftColor={active ? "brand.400" : "transparent"}
      _hover={{
        bg: active ? "sidebar.activeBg" : "sidebar.hover",
        color: active ? "sidebar.activeFg" : "sidebar.fg",
      }}
      transition="background-color 0.2s, color 0.2s"
    >
      {icon}
      <Text fontSize="md" fontWeight={active ? "semibold" : "medium"}>
        {label}
      </Text>
    </Flex>
  );
};

export default MenuItem;
