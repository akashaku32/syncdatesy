import { useEffect, useRef, useState } from "react";

import {
  Button,
  Checkbox,
  Field,
  Fieldset,
  Flex,
  Image,
  Input,
  InputGroup,
  Span,
  Stack,
  Text,
} from "@chakra-ui/react";

import { LuEye, LuEyeOff } from "react-icons/lu";

import { useNavigate } from "react-router";

import Logo from "@/assets/logo.svg";
import { toaster } from "@/components/ui/toaster";
import passwordPolicy from "@/config/password-policy";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

type FormState = {
  firstName: string;
  lastName: string;
  email: string;
  password: string;
  company: string;
  terms: boolean;
};

const initialForm: FormState = {
  firstName: "",
  lastName: "",
  email: "",
  password: "",
  company: "",
  terms: false,
};

const Register = () => {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>(initialForm);
  const [errors, setErrors] = useState<
    Partial<Record<keyof FormState, string>>
  >({});
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (passwordRef.current) {
      passwordRef.current.value = "";
    }
  }, []);

  const [error, setError] = useState<{ field: string; message: string } | null>(
    null,
  );

  const validate = () => {
    const e: Partial<Record<keyof FormState, string>> = {};

    if (!form.firstName.trim()) e.firstName = "First name is required";
    if (!form.lastName.trim()) e.lastName = "Last name is required";

    if (!form.email.trim()) e.email = "Email is required";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i.test(form.email.trim()))
      e.email = "Enter a valid email";

    if (!form.password) e.password = "Password is required";
    else if (!passwordPolicy.passwordRegex.test(form.password))
      e.password = passwordPolicy.passwordPolicyErrorMessage;

    if (!form.company.trim()) e.company = "Company is required";
    if (!form.terms) e.terms = "You must accept the Terms and Privacy policy";

    return e;
  };

  const onChange = (key: keyof FormState) => (value: string | boolean) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: undefined }));

    if (key === "password" && error?.field === "password") setError(null);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const eMap = validate();
    setErrors(eMap);
    if (Object.values(eMap).some(Boolean)) return;

    setSubmitting(true);
    const payload = {
      first_name: form.firstName,
      last_name: form.lastName,
      company_email: form.email,
      password: form.password,
      company_name: form.company,
      employee_range: "1-10",
    };

    try {
      const response = await AxiosInstance.post(ServerRoutes.auth.register(), {
        ...payload,
      });
      toaster.success({
        title: "Registration successful.",
        description:
          response?.data?.message ||
          "A verification code has been sent to your email.",
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.VERIFY_EMAIL}`, {
        state: { email: form.email },
      });
    } catch (err: unknown) {
      const error = err as Record<string, unknown>;

      // Helper to extract first error message from any field
      const getErrorMessage = (value: unknown): string | null => {
        if (Array.isArray(value) && value.length > 0) return String(value[0]);
        if (typeof value === "string" && value.trim()) return value;
        return null;
      };

      // Map backend field names to frontend (email can come as any_email, company_email, etc.)
      const fieldMap: Record<string, keyof FormState> = {
        first_name: "firstName",
        last_name: "lastName",
        company_email: "email",
        any_email: "email",
        email: "email",
        password: "password",
        company_name: "company",
      };

      // Extract field errors
      const fieldErrors: Partial<Record<keyof FormState, string>> = {};
      for (const [key, value] of Object.entries(error)) {
        if (fieldMap[key]) {
          const msg = getErrorMessage(value);
          if (msg) fieldErrors[fieldMap[key]] = msg;
        }
      }

      if (Object.keys(fieldErrors).length > 0) {
        setErrors(fieldErrors);
      }

      // Get error message for toast
      const errorMessage =
        getErrorMessage(error?.non_field_errors) ||
        getErrorMessage(error?.message) ||
        getErrorMessage(error?.error) ||
        getErrorMessage(error?.description) ||
        Object.values(fieldErrors)[0] ||
        "Registration failed";

      toaster.error({
        title: "Registration failed",
        description: errorMessage,
      });
    } finally {
      setSubmitting(false);
    }
  };

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
                Sign Up
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

            {/* First name */}
            <Field.Root required invalid={!!errors.firstName}>
              <Field.Label>First name</Field.Label>
              <Input
                placeholder="Enter your first name"
                value={form.firstName}
                autoComplete="off"
                onChange={(ev) => onChange("firstName")(ev.target.value)}
              />
              <Field.ErrorText>{errors.firstName}</Field.ErrorText>
            </Field.Root>

            {/* Last name */}
            <Field.Root required invalid={!!errors.lastName}>
              <Field.Label>Last name</Field.Label>
              <Input
                placeholder="Enter your last name"
                value={form.lastName}
                autoComplete="off"
                onChange={(ev) => onChange("lastName")(ev.target.value)}
              />
              <Field.ErrorText>{errors.lastName}</Field.ErrorText>
            </Field.Root>

            {/* Email */}
            <Field.Root required invalid={!!errors.email}>
              <Field.Label>Email address</Field.Label>
              <Input
                type="email"
                placeholder="Share your email address"
                value={form.email}
                autoComplete="off"
                onChange={(ev) => onChange("email")(ev.target.value)}
              />
              <Field.ErrorText>{errors.email}</Field.ErrorText>
            </Field.Root>

            {/* Password */}
            <Field.Root
              required
              invalid={!!errors.password || error?.field === "password"}
            >
              <Field.Label>Password</Field.Label>
              <InputGroup
                endElement={
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowPassword((s) => !s)}
                  >
                    {showPassword ? <LuEyeOff /> : <LuEye />}
                  </Button>
                }
              >
                <Input
                  ref={passwordRef}
                  type={showPassword ? "text" : "password"}
                  placeholder="Enter password"
                  value={form.password}
                  autoComplete="new-password"
                  onChange={(ev) => onChange("password")(ev.target.value)}
                />
              </InputGroup>
              <Field.ErrorText>
                {errors.password ||
                  (error?.field === "password" ? error.message : "")}
              </Field.ErrorText>
            </Field.Root>

            {/* Company */}
            <Field.Root required invalid={!!errors.company}>
              <Field.Label>Company</Field.Label>
              <Input
                placeholder="Share your company name"
                value={form.company}
                autoComplete="off"
                onChange={(ev) => onChange("company")(ev.target.value)}
              />
              <Field.ErrorText>{errors.company}</Field.ErrorText>
            </Field.Root>

            {/* Terms */}
            <Flex justifyContent="space-between" alignItems="center" mb={2}>
              <Checkbox.Root
                colorPalette="brand"
                size="md"
                onCheckedChange={(e) => onChange("terms")(!!e.checked)}
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label>
                  I agree with Terms and Privacy policy
                </Checkbox.Label>
              </Checkbox.Root>
            </Flex>
          </Fieldset.Root>

          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            mt={2}
            loading={submitting}
            mb={4}
          >
            Create your account
          </Button>
        </Stack>
      </form>
    </Flex>
  );
};

export default Register;
