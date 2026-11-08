import { GridItem, Text } from "@chakra-ui/react";

import LogoWhite from "@/assets/images/logo-white.svg";
import { APP_NAME, APP_TAGLINE } from "@/constants/branding";

const Banner = () => {
  return (
    <GridItem
      area="banner"
      bg="brand.700"
      backgroundImage="linear-gradient(145deg, #0f766e 0%, #0284c7 55%, #14b8a6 100%)"
      backgroundImage={`url("${LogoWhite}"), url("${LogoWhite}")`}
      backgroundRepeat="no-repeat, no-repeat"
      backgroundPosition="top left, calc(100% - -10px) calc(100% - 20px)"
      backgroundSize="300px, 300px"
      color="white"
      alignItems="center"
      justifyContent="center"
      flexDirection="column"
      textAlign="center"
      gap={2}
      p={{ base: 4, md: 8, lg: 12 }}
      display={{ base: "none", md: "flex" }}
    >
      <Text fontWeight="bold" fontSize="4xl">
        Welcome to {APP_NAME}
      </Text>
      <Text maxW="lg">{APP_TAGLINE}</Text>
    </GridItem>
  );
};
export default Banner;
