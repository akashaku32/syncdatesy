import { useState } from "react";

import { Button, Field, Fieldset, Input, Span } from "@chakra-ui/react";

import { useNavigate } from "react-router";

import { AuthFormCard } from "@/components/auth/AuthFormCard";
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
      <AuthFormCard
        title="Forgot password"
        description={
          <>
            Enter your work email and we&apos;ll send reset instructions.{" "}
            <Span
              color="brand.600"
              fontWeight="semibold"
              cursor="pointer"
              onClick={() =>
                navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
              }
            >
              Back to sign in
            </Span>
          </>
        }
      >
        <Fieldset.Root size="lg" w="100%" invalid={hasError} gap={4}>
          <Fieldset.Content>
            <Field.Root required>
              <Field.Label>Work email</Field.Label>
              <Input
                name="email"
                type="email"
                size="lg"
                borderRadius="lg"
                placeholder="you@company.com"
                autoFocus
                autoComplete="off"
              />
            </Field.Root>
          </Fieldset.Content>
          <Fieldset.ErrorText>Enter a valid email address</Fieldset.ErrorText>
          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            w="full"
            borderRadius="lg"
            loading={isLoading}
          >
            Send reset link
          </Button>
        </Fieldset.Root>
      </AuthFormCard>
    </form>
  );
}
