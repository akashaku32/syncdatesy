import { GridItem, Text } from "@chakra-ui/react";

import LogoWhite from "@/assets/images/logo-white.svg";

const Banner = () => {
  return (
    <GridItem
      area="banner"
      background="brand.500"
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
        Welcome to Datasyncher
      </Text>
      <Text>
        Managing and syncing data across platforms has never been easier.
        Datasyncher simplifies your data migration, ETL processes, and real-time
        synchronization so your business can focus on what truly matters.
      </Text>
    </GridItem>
  );
};
export default Banner;
