import { useEffect, useReducer, useState } from "react";

import {
  Button,
  Field,
  Fieldset,
  Flex,
  Input,
  Stack,
  chakra,
} from "@chakra-ui/react";

import { useNavigate, useParams } from "react-router";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { PasswordInput } from "@/components/ui/password-input";
import { toaster } from "@/components/ui/toaster";
import passwordPolicy from "@/config/password-policy";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import useCreateUser from "@/queryOptions/user/useCreateMember";
import useFetchUserById from "@/queryOptions/user/useFetchUserById";
import { useUpdateUser } from "@/queryOptions/user/useUpdateUserById";

import RoleDropdown from "./Role";
import { BreadcrumbsForEditUser, BreadcrumbsForNewUser } from "./helper";
import { type UserState, initialState, userReducer } from "./reducer";

interface ApiErrorResponse {
  non_field_errors?: string[];
  error?: string;
  message?: string;
  [key: string]: string | string[] | undefined;
}

const UserForm = ({ mode }: { mode: "edit" | "add" }) => {
  const navigate = useNavigate();
  const [formState, dispatch] = useReducer(userReducer, initialState);
  const [error, setError] = useState<{
    message: string;
    field: keyof UserState;
  } | null>(null);
  const params = useParams<{ userId: string }>();
  const { data: userData, isLoading: isFetchUserLoading } = useFetchUserById(
    Number(params.userId),
  );
  const { mutate: updateUser, isPending: isUpdateUserPending } = useUpdateUser({
    id: Number(params.userId),
  });
  const { mutate: createUser, isPending: isCreateUserPending } =
    useCreateUser();

  useEffect(() => {
    // Fetch user data if in edit mode
    if (userData && mode === "edit") {
      dispatch({
        type: "SET_USER",
        payload: {
          firstName: userData.first_name,
          lastName: userData.last_name,
          companyEmail: userData.company_email,
          companyName: userData.company_name,
          role: userData.role,
          password: "",
          confirmPassword: "",
        },
      });
    }
  }, [mode, userData]);

  // Validate password policy on blur
  const handlePasswordBlur = ({ field }: { field: keyof UserState }) => {
    if (!passwordPolicy.passwordRegex.test(formState[field])) {
      setError({
        message: passwordPolicy.passwordPolicyErrorMessage,
        field: field,
      });
    } else if (error?.field === field) {
      setError(null);
    }
  };

  const handleInputChange =
    (field: keyof UserState) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      dispatch({ type: "UPDATE_FIELD", field, value: event.target.value });
    };

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    // Password match check
    if (mode === "add" && formState.password !== formState.confirmPassword) {
      setError({ message: "Passwords do not match", field: "confirmPassword" });
      return;
    }
    if (
      mode === "add" &&
      !passwordPolicy.passwordRegex.test(formState.password)
    ) {
      setError({
        message: passwordPolicy.passwordPolicyErrorMessage,
        field: "password",
      });
      return;
    }
    if (!formState.role) {
      setError({ message: "Role is required", field: "role" });
      return;
    }
    setError(null);
    // Handle Add User submission
    if (mode === "add") {
      createUser(
        {
          first_name: formState.firstName,
          last_name: formState.lastName,
          company_email: formState.companyEmail,
          password: formState.password,
          confirm_password: formState.confirmPassword,
          role: formState.role,
        },
        {
          onSuccess: () => {
            toaster.success({
              title: "Member created successfully",
            });
            navigate(
              `${ClientRoutes.DASHBOARD}/${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}`,
            );
          },
          onError: (err: unknown) => {
            const data = err as ApiErrorResponse;
            let errorMessage =
              data?.non_field_errors?.[0] ||
              data?.error ||
              data?.message ||
              "Error creating member";

            if (
              !data?.non_field_errors &&
              !data?.error &&
              !data?.message &&
              typeof data === "object"
            ) {
              const keys = Object.keys(data);
              if (keys.length > 0) {
                const firstError = data[keys[0]];
                errorMessage = Array.isArray(firstError)
                  ? firstError[0]
                  : (firstError ?? "Error creating member");
              }
            }

            toaster.error({
              title: errorMessage,
            });

            // Map backend fields to frontend state keys
            const fieldMap: Record<string, keyof UserState> = {
              company_email: "companyEmail",
              first_name: "firstName",
              last_name: "lastName",
              role: "role",
              password: "password",
            };

            Object.keys(data).forEach((key) => {
              if (fieldMap[key]) {
                const message = Array.isArray(data[key])
                  ? data[key][0]
                  : (data[key] ?? "Error");
                setError({
                  message: message,
                  field: fieldMap[key],
                });
              }
            });
          },
        },
      );
    }
    // Handle Edit User submission
    if (mode === "edit") {
      updateUser(
        {
          first_name: formState.firstName,
          last_name: formState.lastName,
          company_email: formState.companyEmail,
          password: formState.password,
          confirm_password: formState.confirmPassword,
          role: formState.role,
        },
        {
          onSuccess: () => {
            toaster.success({
              title: "Member updated successfully",
            });
            navigate(
              `${ClientRoutes.DASHBOARD}/${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}`,
            );
          },
          onError: (err: unknown) => {
            const error = err as ApiErrorResponse;
            let errorMessage =
              error?.message || error?.error || "Error updating member";

            // Check if error is a field error object
            if (!error?.message && !error?.error && typeof error === "object") {
              const keys = Object.keys(error);
              if (keys.length > 0) {
                const firstError = error[keys[0]];
                errorMessage = Array.isArray(firstError)
                  ? firstError[0]
                  : (firstError ?? "Error updating member");
              }
            }

            toaster.error({
              title: errorMessage,
            });

            // Map backend fields to frontend state keys
            const fieldMap: Record<string, keyof UserState> = {
              company_email: "companyEmail",
              first_name: "firstName",
              last_name: "lastName",
              role: "role",
              password: "password",
            };

            Object.keys(error).forEach((key) => {
              if (fieldMap[key]) {
                const errVal = error[key];
                const message = Array.isArray(errVal)
                  ? errVal[0]
                  : (errVal ?? "Error");
                setError({
                  message: message,
                  field: fieldMap[key],
                });
              }
            });
          },
        },
      );
    }
  };

  const handleRoleChange = (value: string) => {
    dispatch({ type: "UPDATE_FIELD", field: "role", value });
  };

  if (isFetchUserLoading) {
    return <LoadingSpinner />;
  }

  return (
    <Flex flexDirection="column" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={
          mode === "add" ? BreadcrumbsForNewUser : BreadcrumbsForEditUser
        }
        title={mode === "add" ? `Add member` : `Edit member`}
      />
      <chakra.form
        onSubmit={onSubmit}
        maxW={{ base: "100%", md: "500px" }}
        autoComplete="off"
        width="100%"
        display="flex"
        flexDirection="column"
        gap={4}
      >
        <Stack gap={4}>
          <Fieldset.Root size="md" gap={4}>
            <Fieldset.Content>
              {/** Company email */}
              <Field.Root required invalid={error?.field === "companyEmail"}>
                <Field.Label>
                  Company email <Field.RequiredIndicator />
                </Field.Label>
                <Input
                  type="email"
                  placeholder="Enter your company email"
                  value={formState.companyEmail}
                  onChange={handleInputChange("companyEmail")}
                  disabled={mode === "edit"}
                  readOnly={mode === "edit"}
                />
              </Field.Root>
              {/** First Name */}
              <Field.Root required invalid={error?.field === "firstName"}>
                <Field.Label>
                  First Name <Field.RequiredIndicator />
                </Field.Label>
                <Input
                  type="text"
                  placeholder="Enter your first name"
                  value={formState.firstName}
                  onChange={handleInputChange("firstName")}
                />
              </Field.Root>
              {/** Last Name */}
              <Field.Root required invalid={error?.field === "lastName"}>
                <Field.Label>
                  Last Name <Field.RequiredIndicator />
                </Field.Label>
                <Input
                  type="text"
                  placeholder="Enter your last name"
                  value={formState.lastName}
                  onChange={handleInputChange("lastName")}
                />
              </Field.Root>
              {/** Password */}
              {mode === "add" && (
                <Field.Root required invalid={error?.field === "password"}>
                  <Field.Label>
                    Password <Field.RequiredIndicator />
                  </Field.Label>
                  <PasswordInput
                    placeholder="Enter your password"
                    value={formState.password}
                    onChange={handleInputChange("password")}
                    onBlur={() => handlePasswordBlur({ field: "password" })}
                  />
                  <Field.ErrorText>{error?.message}</Field.ErrorText>
                </Field.Root>
              )}
              {/** Confirm Password */}
              {mode === "add" && (
                <Field.Root
                  required
                  invalid={error?.field === "confirmPassword"}
                >
                  <Field.Label>
                    Confirm Password <Field.RequiredIndicator />
                  </Field.Label>
                  <PasswordInput
                    placeholder="Confirm your password"
                    value={formState.confirmPassword}
                    onChange={handleInputChange("confirmPassword")}
                    onBlur={() => handlePasswordBlur({ field: "password" })}
                  />
                  <Field.ErrorText>{error?.message}</Field.ErrorText>
                </Field.Root>
              )}
              <RoleDropdown
                handleRoleChange={handleRoleChange}
                formState={formState}
                error={error}
              />
            </Fieldset.Content>
          </Fieldset.Root>
          <Button
            type="submit"
            colorPalette="brand"
            alignSelf="flex-end"
            mt={2}
            loading={isCreateUserPending || isUpdateUserPending}
          >
            {mode === "add" ? "Add Member" : "Update"}
          </Button>
        </Stack>
      </chakra.form>
    </Flex>
  );
};

export default UserForm;
