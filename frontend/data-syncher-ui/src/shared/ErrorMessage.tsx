import { Box } from "@chakra-ui/react";

const ErrorMessage = ({ message }: { message: string }) => (
  <Box color="red.500" p={4} bg="red.50" borderRadius="md">
    {message}
  </Box>
);
export default ErrorMessage;
