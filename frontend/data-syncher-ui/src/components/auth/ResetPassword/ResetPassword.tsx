import { useEffect, useState } from "react";

import {
  Button,
  Field,
  Fieldset,
  Flex,
  Image,
  Span,
  Stack,
  Text,
} from "@chakra-ui/react";

import { useNavigate, useSearchParams } from "react-router";

import Logo from "@/assets/logo.svg";
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
    // if not token present in url, show Invalid URL message
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

  // Validate password policy on blur
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
    // Check if password and confirm password match
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
      toaster.error({
        title: "Password Reset Failed",
        description:
          "There was an error resetting your password. Please try again.",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Stack as="form" style={{ width: "100%" }} onSubmit={handleSubmit}>
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
              Reset your password
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
            <Field.Root required invalid={error?.field === "password"}>
              <Field.Label>
                Password <Field.RequiredIndicator />
              </Field.Label>
              <PasswordInput
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onBlur={() => handlePasswordBlur({ field: "password" })}
              />
              <Field.ErrorText>{error?.message}</Field.ErrorText>
            </Field.Root>
            <Field.Root required invalid={error?.field === "confirmPassword"}>
              <Field.Label>
                Confirm Password <Field.RequiredIndicator />
              </Field.Label>
              <PasswordInput
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
            alignSelf="center"
            colorPalette="brand"
            w="70%"
            loading={isLoading}
          >
            Submit
          </Button>
        </Fieldset.Root>
      </Flex>
    </Stack>
  );
};
export default ResetPassword;
