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
      w="full"
      paddingInline={0}
      paddingBlock={0}
    >
      {children}
    </Flex>
  );
}
