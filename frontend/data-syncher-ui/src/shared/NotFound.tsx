import { Button, Flex, Image } from "@chakra-ui/react";

import { useNavigate } from "react-router";

import NotFoundIllustration from "@/assets/images/not-found-error-illustration.svg";
import ClientRoutes from "@/constants/client-routes";

const NotFound = () => {
  const navigate = useNavigate();

  return (
    <Flex justify="center" align="center" height="100vh" direction={"column"}>
      <Image
        src={NotFoundIllustration}
        alt="Not Found"
        boxSize="300px"
        objectFit="cover"
      />
      <Button
        mt={4}
        colorPalette="brand"
        onClick={() => navigate(ClientRoutes.DASHBOARD)}
      >
        Go to Dashboard
      </Button>
    </Flex>
  );
};

export default NotFound;
