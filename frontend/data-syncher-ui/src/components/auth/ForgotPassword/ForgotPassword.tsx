import { useState } from "react";

import {
  Button,
  Field,
  Fieldset,
  Flex,
  Image,
  Input,
  Span,
  Stack,
  Text,
} from "@chakra-ui/react";

import { useNavigate } from "react-router";

import Logo from "@/assets/logo.svg";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

export default function ForgotPassword() {
  const navigate = useNavigate();

  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // Handle form submission logic here
    const formData = new FormData(event.currentTarget);
    const data = {
      email: formData.get("email") as string,
    };
    if (!data.email) {
      setHasError(true);
      return;
    }
    try {
      setIsLoading(true);
      setHasError(false);
      await AxiosInstance({
        method: "POST",
        url: ServerRoutes.auth.forgotPassword(),
        data,
      });
      toaster.success({
        title: "Password reset email sent.",
        description:
          "Please check your email for instructions to reset your password.",
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
    } catch {
      setHasError(true);
      setIsLoading(false);
      toaster.error({
        title: "Error",
        description: "Failed to send password reset email.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form style={{ width: "100%" }} onSubmit={handleSubmit} autoComplete="off">
      <Flex justifyContent="center" alignItems="center">
        <Fieldset.Root
          size="lg"
          w="100%"
          justifyContent="center"
          invalid={hasError}
        >
          <Stack gap={4} mb={2}>
            <Image
              src={Logo}
              alt="Logo"
              width="48px"
              height="48px"
              aspectRatio={1}
            />
            <Fieldset.Legend fontSize={32} fontWeight="bold">
              Sign in
            </Fieldset.Legend>
            <Text fontSize="sm" color="gray.500">
              Already have an account?{" "}
              <Span
                color="brand.500"
                cursor="pointer"
                onClick={() =>
                  navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
                }
              >
                Sign in
              </Span>
            </Text>
          </Stack>
          <Fieldset.Content>
            <Field.Root required>
              <Field.Label fontSize="16px">
                Email Id <Field.RequiredIndicator />
              </Field.Label>
              <Input
                name="email"
                type="email"
                placeholder="Enter your email id"
                autoFocus
                autoComplete="off"
              />
            </Field.Root>
          </Fieldset.Content>
          <Fieldset.ErrorText>Invalid Email ID</Fieldset.ErrorText>
          <Button
            type="submit"
            alignSelf="center"
            colorPalette="brand"
            w="70%"
            loading={isLoading}
          >
            Submit
          </Button>
        </Fieldset.Root>
      </Flex>
    </form>
  );
}
