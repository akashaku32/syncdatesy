import { useState } from "react";

import {
  Button,
  Field,
  Fieldset,
  Flex,
  Heading,
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

type FormState = {
  firstName: string;
  lastName: string;
  email: string;
  phoneNumber: string;
  companyName: string;
};

type RegistrationRequestResponse = {
  message?: string;
  registration_request_id?: number;
  [key: string]: unknown;
};

type SubmissionResult = {
  message: string;
};

const REQUEST_SUBMITTED_MESSAGE =
  "Your request has been submitted and is awaiting approval. You'll receive your login credentials after access is provided.";

const initialForm: FormState = {
  firstName: "",
  lastName: "",
  email: "",
  phoneNumber: "",
  companyName: "",
};

const getErrorMessage = (value: unknown): string | null => {
  if (Array.isArray(value) && value.length > 0) return String(value[0]);
  if (typeof value === "string" && value.trim()) return value;
  return null;
};

const RequestTrialAccess = () => {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(initialForm);
  const [errors, setErrors] = useState<
    Partial<Record<keyof FormState, string>>
  >({});
  const [submitting, setSubmitting] = useState(false);
  const [submissionResult, setSubmissionResult] =
    useState<SubmissionResult | null>(null);

  const validate = () => {
    const nextErrors: Partial<Record<keyof FormState, string>> = {};

    if (!form.firstName.trim()) nextErrors.firstName = "First name is required";
    if (!form.lastName.trim()) nextErrors.lastName = "Last name is required";

    if (!form.email.trim()) nextErrors.email = "Email is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i.test(form.email.trim()))
      nextErrors.email = "Enter a valid email";

    const normalizedPhone = form.phoneNumber.replace(/[\s()-]/g, "");
    if (normalizedPhone && !/^\+?\d{7,15}$/.test(normalizedPhone))
      nextErrors.phoneNumber = "Enter a valid phone number";
    if (!form.companyName.trim())
      nextErrors.companyName = "Company name is required";

    return nextErrors;
  };

  const onChange = (key: keyof FormState) => (value: string) => {
    setForm((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: undefined }));
  };

  const applyBackendErrors = (data: RegistrationRequestResponse) => {
    const fieldMap: Record<string, keyof FormState> = {
      first_name: "firstName",
      last_name: "lastName",
      email: "email",
      phone_number: "phoneNumber",
      company_name: "companyName",
    };
    const fieldErrors: Partial<Record<keyof FormState, string>> = {};

    Object.entries(data).forEach(([key, value]) => {
      const field = fieldMap[key];
      const message = getErrorMessage(value);
      if (field && message) fieldErrors[field] = message;
    });

    setErrors(fieldErrors);
    return (
      Object.values(fieldErrors)[0] ||
      getErrorMessage(data.non_field_errors) ||
      getErrorMessage(data.message) ||
      "Please check the entered details."
    );
  };

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextErrors = validate();
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;

    const email = form.email.trim();
    const phoneNumber = form.phoneNumber.replace(/[\s()-]/g, "");
    setSubmitting(true);

    try {
      const response = await AxiosInstance.post<RegistrationRequestResponse>(
        ServerRoutes.auth.registrationRequest(),
        {
          first_name: form.firstName.trim(),
          last_name: form.lastName.trim(),
          email,
          ...(phoneNumber ? { phone_number: phoneNumber } : {}),
          company_name: form.companyName.trim(),
        },
        {
          validateStatus: (status) =>
            (status >= 200 && status < 300) || status === 400 || status === 503,
        },
      );

      if (response.status === 400) {
        toaster.error({
          title: "Unable to submit request",
          description: applyBackendErrors(response.data),
        });
        return;
      }

      if (response.status === 503) {
        setSubmissionResult({
          message: REQUEST_SUBMITTED_MESSAGE,
        });
        toaster.success({
          title: "Trial access request submitted",
          description: REQUEST_SUBMITTED_MESSAGE,
        });
        return;
      }

      const message = REQUEST_SUBMITTED_MESSAGE;
      setSubmissionResult({ message });
      toaster.success({
        title: "Trial access request submitted",
        description: message,
      });
    } catch (error: unknown) {
      const data = (error || {}) as RegistrationRequestResponse;
      toaster.error({
        title: "Unable to submit request",
        description:
          getErrorMessage(data.message) ||
          getErrorMessage(data.error) ||
          "Please try again later.",
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (submissionResult) {
    return (
      <Flex direction="column" w="100%" h="100%">
        <Stack gap={5} align="flex-start" role="status" aria-live="polite">
          <Image src={Logo} alt="Logo" width="48px" height="48px" />
          <Heading as="h1" fontSize={32} fontWeight="semibold">
            Request received
          </Heading>
          <Text color="gray.600">{submissionResult.message}</Text>
          <Button
            colorPalette="brand"
            onClick={() =>
              navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
            }
          >
            Back to sign in
          </Button>
        </Stack>
      </Flex>
    );
  }

  return (
    <Flex direction="column" w="100%" h="100%">
      <form onSubmit={onSubmit} autoComplete="off" style={{ width: "100%" }}>
        <Stack gap={2}>
          <Fieldset.Root size="md" gap={2}>
            <Stack gap={4} mb={2}>
              <Image
                src={Logo}
                alt="Logo"
                width="48px"
                height="48px"
                aspectRatio={1}
              />
              <Fieldset.Legend fontSize={32} fontWeight="semibold">
                Request Trial Access
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

            <Field.Root required invalid={!!errors.companyName}>
              <Field.Label>Company</Field.Label>
              <Input
                placeholder="Share your company name"
                value={form.companyName}
                autoComplete="organization"
                onChange={(event) =>
                  onChange("companyName")(event.target.value)
                }
              />
              <Field.ErrorText>{errors.companyName}</Field.ErrorText>
            </Field.Root>
          </Fieldset.Root>

          <Field.Root required invalid={!!errors.firstName}>
            <Field.Label>First name</Field.Label>
            <Input
              placeholder="Enter your first name"
              value={form.firstName}
              autoComplete="given-name"
              onChange={(event) => onChange("firstName")(event.target.value)}
            />
            <Field.ErrorText>{errors.firstName}</Field.ErrorText>
          </Field.Root>

          <Field.Root required invalid={!!errors.lastName}>
            <Field.Label>Last name</Field.Label>
            <Input
              placeholder="Enter your last name"
              value={form.lastName}
              autoComplete="family-name"
              onChange={(event) => onChange("lastName")(event.target.value)}
            />
            <Field.ErrorText>{errors.lastName}</Field.ErrorText>
          </Field.Root>

          <Field.Root required invalid={!!errors.email}>
            <Field.Label>Email address</Field.Label>
            <Input
              type="email"
              placeholder="Share your email address"
              value={form.email}
              autoComplete="email"
              onChange={(event) => onChange("email")(event.target.value)}
            />
            <Field.ErrorText>{errors.email}</Field.ErrorText>
          </Field.Root>

          <Field.Root invalid={!!errors.phoneNumber}>
            <Field.Label>Phone number</Field.Label>
            <Input
              type="tel"
              inputMode="tel"
              placeholder="Enter your phone number"
              value={form.phoneNumber}
              autoComplete="tel"
              maxLength={20}
              onChange={(event) => onChange("phoneNumber")(event.target.value)}
            />
            <Field.ErrorText>{errors.phoneNumber}</Field.ErrorText>
          </Field.Root>

          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            mt={2}
            loading={submitting}
            mb={4}
          >
            Request for trial access
          </Button>
        </Stack>
      </form>
    </Flex>
  );
};

export default RequestTrialAccess;
