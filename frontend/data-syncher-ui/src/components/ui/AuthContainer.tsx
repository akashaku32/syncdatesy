import { ReactNode } from "react";

import { Box, GridItem, GridItemProps } from "@chakra-ui/react";

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
      position="relative"
      overflowY="auto"
      minH={{ base: "auto", md: "100dvh" }}
      display="flex"
      flexDirection="column"
      justifyContent={{ base: "flex-start", md: "center" }}
      alignItems="center"
      px={{ base: 4, md: 10, lg: 14 }}
      py={{ base: 6, md: 10 }}
      bg="bg.auth"
    >
      <Box
        aria-hidden
        position="absolute"
        top={0}
        right={0}
        w="70%"
        h="50%"
        pointerEvents="none"
        opacity={0.5}
        bgGradient="radial(circle at 100% 0%, brand.100, transparent 65%)"
      />
      <Box position="relative" zIndex={1} w="full" display="flex" flexDirection="column" alignItems="stretch">
        {children}
      </Box>
    </GridItem>
  );
}
