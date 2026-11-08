import { ReactNode } from "react";

import { GridItem, GridItemProps } from "@chakra-ui/react";

type AuthContainerProps = GridItemProps & {
  children: ReactNode;
};

export default function AuthContainer({
  children,
  ...props
}: AuthContainerProps) {
  return (
    <GridItem
      area="main"
      as="main"
      {...props}
      p={{ base: 6, md: 8 }}
      bg="white"
      justifyContent={"center"}
      alignItems={"center"}
      display={"flex"}
      maxW="600px"
      overflowY={"auto"}
    >
      {children}
    </GridItem>
  );
}
