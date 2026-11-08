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
      p={{ base: 5, md: 10 }}
      bg="bg.canvas"
      justifyContent="center"
      alignItems="center"
      display="flex"
      overflowY="auto"
      minH={0}
    >
      {children}
    </GridItem>
  );
}
