import { useState } from "react";

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

import { useNavigate } from "react-router";

import Logo from "@/assets/logo.svg";
import { PasswordInput } from "@/components/ui/password-input";
import passwordPolicy from "@/config/password-policy";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

type PasswordResetFormProps = {
  uid: string;
  token: string;
  title: string;
  submitLabel: string;
  onSuccess: (_redirectTo?: string) => void;
};

type PasswordResetResponse = {
  message?: string;
  redirect_to?: string;
};

type PasswordResetErrorResponse = {
  new_password?: unknown;
  confirm_password?: unknown;
  non_field_errors?: unknown;
  error?: unknown;
  message?: unknown;
  detail?: unknown;
};

type FormErrors = {
  newPassword?: string;
  confirmPassword?: string;
  general?: string;
};

const getErrorMessage = (value: unknown): string | undefined => {
  if (Array.isArray(value) && value.length > 0) return String(value[0]);
  if (typeof value === "string" && value.trim()) return value;
  return undefined;
};

const getErrorResponse = (error: unknown): PasswordResetErrorResponse => {
  if (!error || typeof error !== "object") return {};

  if ("response" in error) {
    const response = (error as { response?: { data?: unknown } }).response;
    if (response?.data && typeof response.data === "object") {
      return response.data as PasswordResetErrorResponse;
    }
  }

  return error as PasswordResetErrorResponse;
};

const PasswordResetForm = ({
  uid,
  token,
  title,
  submitLabel,
  onSuccess,
}: PasswordResetFormProps) => {
  const navigate = useNavigate();
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [isLoading, setIsLoading] = useState(false);

  const clearError = (field: keyof FormErrors) => {
    setErrors((current) => ({
      ...current,
      [field]: undefined,
      general: undefined,
    }));
  };

  const validate = () => {
    const nextErrors: FormErrors = {};

    if (!newPassword) {
      nextErrors.newPassword = "New password is required.";
    } else if (!passwordPolicy.passwordRegex.test(newPassword)) {
      nextErrors.newPassword = passwordPolicy.passwordPolicyErrorMessage;
    }

    if (!confirmPassword) {
      nextErrors.confirmPassword = "Please confirm your new password.";
    } else if (newPassword !== confirmPassword) {
      nextErrors.confirmPassword = "Passwords do not match.";
    }

    return nextErrors;
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();

    const validationErrors = validate();
    setErrors(validationErrors);
    if (Object.values(validationErrors).some(Boolean)) return;

    setIsLoading(true);
    setErrors({});

    try {
      const { data } = await AxiosInstance.post<PasswordResetResponse>(
        ServerRoutes.auth.resetPassword(),
        {
          uid,
          token,
          new_password: newPassword,
          confirm_password: confirmPassword,
        },
      );

      setNewPassword("");
      setConfirmPassword("");
      onSuccess(data.redirect_to);
    } catch (error: unknown) {
      const data = getErrorResponse(error);
      const newPasswordError = getErrorMessage(data.new_password);
      const confirmPasswordError = getErrorMessage(data.confirm_password);
      const generalError =
        getErrorMessage(data.non_field_errors) ||
        getErrorMessage(data.error) ||
        getErrorMessage(data.detail) ||
        getErrorMessage(data.message);

      setErrors({
        newPassword: newPasswordError,
        confirmPassword: confirmPasswordError,
        general:
          generalError ||
          (!newPasswordError && !confirmPasswordError
            ? "Unable to reset your password. Please try again."
            : undefined),
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Stack as="form" style={{ width: "100%" }} onSubmit={handleSubmit}>
      <Flex justifyContent="center" alignItems="center">
        <Fieldset.Root size="lg" w="100%" justifyContent="center">
          <Stack gap={4} mb={2}>
            <Image
              src={Logo}
              alt="Logo"
              width="48px"
              height="48px"
              aspectRatio={1}
            />
            <Fieldset.Legend fontSize={32} fontWeight="bold">
              {title}
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
            <Field.Root required invalid={Boolean(errors.newPassword)}>
              <Field.Label>
                New password <Field.RequiredIndicator />
              </Field.Label>
              <PasswordInput
                name="newPassword"
                placeholder="Enter your new password"
                value={newPassword}
                autoComplete="new-password"
                onChange={(event) => {
                  setNewPassword(event.target.value);
                  clearError("newPassword");
                }}
              />
              <Field.ErrorText>{errors.newPassword}</Field.ErrorText>
            </Field.Root>

            <Field.Root required invalid={Boolean(errors.confirmPassword)}>
              <Field.Label>
                Confirm new password <Field.RequiredIndicator />
              </Field.Label>
              <PasswordInput
                name="confirmPassword"
                placeholder="Confirm your new password"
                value={confirmPassword}
                autoComplete="new-password"
                onChange={(event) => {
                  setConfirmPassword(event.target.value);
                  clearError("confirmPassword");
                }}
              />
              <Field.ErrorText>{errors.confirmPassword}</Field.ErrorText>
            </Field.Root>
          </Fieldset.Content>

          {errors.general && (
            <Fieldset.ErrorText role="alert">
              {errors.general}
            </Fieldset.ErrorText>
          )}

          <Button
            type="submit"
            alignSelf="center"
            colorPalette="brand"
            w="70%"
            loading={isLoading}
          >
            {submitLabel}
          </Button>
        </Fieldset.Root>
      </Flex>
    </Stack>
  );
};

export default PasswordResetForm;
