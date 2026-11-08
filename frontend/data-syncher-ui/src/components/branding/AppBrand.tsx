import { Flex, Image, Text, type FlexProps } from "@chakra-ui/react";

import Logo from "@/assets/logo.svg";
import { APP_LOGO_ALT, APP_NAME } from "@/constants/branding";

const LOGO_PX = { sm: "32px", md: "40px", lg: "48px" } as const;

type AppBrandProps = FlexProps & {
  size?: keyof typeof LOGO_PX;
  showName?: boolean;
  nameColor?: string;
  nameSize?: string;
  onClick?: () => void;
};

export function AppBrand({
  size = "md",
  showName = true,
  nameColor = "brand.500",
  nameSize = "2xl",
  onClick,
  ...flexProps
}: AppBrandProps) {
  const px = LOGO_PX[size];
  return (
    <Flex
      alignItems="center"
      gap={2}
      cursor={onClick ? "pointer" : undefined}
      onClick={onClick}
      {...flexProps}
    >
      <Image
        src={Logo}
        alt={APP_LOGO_ALT}
        width={px}
        height={px}
        transition="transform 0.2s"
        _hover={onClick ? { transform: "scale(1.05)" } : undefined}
      />
      {showName ? (
        <Text fontSize={nameSize} fontWeight="semibold" color={nameColor}>
          {APP_NAME}
        </Text>
      ) : null}
    </Flex>
  );
}
