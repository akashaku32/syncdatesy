import { useEffect, useState } from "react";

import { Button, Field, Fieldset, Span } from "@chakra-ui/react";

import { useNavigate, useSearchParams } from "react-router";

import { AuthFormCard } from "@/components/auth/AuthFormCard";
import { PasswordInput } from "@/components/ui/password-input";
import { toaster } from "@/components/ui/toaster";
import passwordPolicy from "@/config/password-policy";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

const ResetPassword = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const uid = searchParams.get("uid");
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!token || !uid) {
      toaster.error({
        title: "Invalid URL",
        description: "The password reset link is invalid.",
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
    }
  }, [token, navigate, uid]);

  const [hasError, setHasError] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<{
    message: string;
    field: "password" | "confirmPassword" | null;
  } | null>(null);

  const handlePasswordBlur = ({
    field,
  }: {
    field: "password" | "confirmPassword";
  }) => {
    if (
      !passwordPolicy.passwordRegex.test(
        field === "password" ? password : confirmPassword,
      )
    ) {
      setError({
        message: passwordPolicy.passwordPolicyErrorMessage,
        field: field,
      });
    } else if (error?.field === field) {
      setError(null);
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password !== confirmPassword) {
      setError({
        message: "Passwords do not match",
        field: "confirmPassword",
      });
      setHasError(true);
      return;
    }

    if (!passwordPolicy.passwordRegex.test(password)) {
      setError({
        message: passwordPolicy.passwordPolicyErrorMessage,
        field: "password",
      });
      setHasError(true);
      return;
    }

    if (!passwordPolicy.passwordRegex.test(confirmPassword)) {
      setError({
        message: passwordPolicy.passwordPolicyErrorMessage,
        field: "confirmPassword",
      });
      setHasError(true);
      return;
    }

    try {
      setIsLoading(true);
      setHasError(false);
      await AxiosInstance.post(ServerRoutes.auth.resetPassword(), {
        new_password: password,
        token,
        uid,
      });
      toaster.success({
        title: "Password Reset Successful",
        description: "Your password has been reset successfully.",
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
    } catch {
      setHasError(true);
      toaster.error({
        title: "Error",
        description:
          "There was an error resetting your password. Please try again.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form style={{ width: "100%" }} onSubmit={handleSubmit}>
      <AuthFormCard
        title="Reset your password"
        description={
          <>
            Choose a strong new password.{" "}
            <Span
              color="brand.600"
              fontWeight="semibold"
              cursor="pointer"
              onClick={() =>
                navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
              }
            >
              Sign in
            </Span>
          </>
        }
      >
        <Fieldset.Root size="lg" w="100%" invalid={hasError} gap={4}>
          <Fieldset.Content gap={4}>
            <Field.Root required invalid={error?.field === "password"}>
              <Field.Label>New password</Field.Label>
              <PasswordInput
                size="lg"
                borderRadius="lg"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onBlur={() => handlePasswordBlur({ field: "password" })}
              />
              <Field.ErrorText>{error?.message}</Field.ErrorText>
            </Field.Root>
            <Field.Root required invalid={error?.field === "confirmPassword"}>
              <Field.Label>Confirm password</Field.Label>
              <PasswordInput
                size="lg"
                borderRadius="lg"
                placeholder="Confirm your password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                onBlur={() => handlePasswordBlur({ field: "confirmPassword" })}
              />
              <Field.ErrorText>{error?.message}</Field.ErrorText>
            </Field.Root>
          </Fieldset.Content>
          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            w="full"
            borderRadius="lg"
            loading={isLoading}
          >
            Update password
          </Button>
        </Fieldset.Root>
      </AuthFormCard>
    </form>
  );
};
export default ResetPassword;
