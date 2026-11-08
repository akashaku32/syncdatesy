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
      cursor={"pointer"}
      gap={2}
      onClick={() => {
        void handleClick();
      }}
      color={active ? "brand.accentOrange" : "white"}
      _hover={{
        bgColor: "gray.600",
        color: active ? "brand.accentOrange" : "white",
      }}
      transition="background-color 0.2s, color 0.2s"
    >
      {icon}
      <Text fontSize="lg">{label}</Text>
    </Flex>
  );
};

export default MenuItem;
