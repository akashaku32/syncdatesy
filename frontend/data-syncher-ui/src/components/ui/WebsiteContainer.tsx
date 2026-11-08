import { ReactNode } from "react";

import { Flex } from "@chakra-ui/react";

export default function Container({ children }: { children: ReactNode }) {
  return (
    <Flex
      flex={1}
      flexDirection="column"
      alignItems="center"
      as="main"
      overflowY="auto"
      paddingInline={{ base: 3, md: 8 }}
      paddingBlock={{ base: 4, md: 8 }}
    >
      {children}
    </Flex>
  );
}
