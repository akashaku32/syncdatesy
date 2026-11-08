import { Flex, Image, Text } from "@chakra-ui/react";

import { VIEW_CONFIG } from "@/constants/view-config";

import NoConnectionsIllustration from "../assets/empty.svg";

const NoConnections = () => {
  return (
    <Flex
      h="100%"
      justifyContent="center"
      alignItems="center"
      flexDirection="column"
      gap={VIEW_CONFIG.pageGap}
    >
      <Image src={NoConnectionsIllustration} alt="No connections" />
      <Flex textAlign={"center"} flexDirection={"column"} gap={2}>
        <Text fontWeight="bold">
          You do not have any connectors in your account
        </Text>
        <Text>Please add a connector to your account</Text>
      </Flex>
    </Flex>
  );
};

export default NoConnections;
