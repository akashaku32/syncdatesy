import { useEffect, useState } from "react";

import {
  Button,
  Field,
  Fieldset,
  HStack,
  Input,
  Span,
  Stack,
  Text,
} from "@chakra-ui/react";

import { AxiosError } from "axios";
import { useLocation, useNavigate, useSearchParams } from "react-router";

import { AuthFormCard } from "@/components/auth/AuthFormCard";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

const VerifyEmail = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [otp, setOtp] = useState("");
  const [otpValues, setOtpValues] = useState<string[]>(new Array(6).fill(""));
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    const state = location.state as { email?: string } | null;
    const queryEmail = searchParams.get("email");

    if (state?.email) {
      setEmail(state.email);
    } else if (queryEmail) {
      setEmail(queryEmail);
    } else {
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
    }
  }, [location, searchParams, navigate]);

  useEffect(() => {
    if (cooldown > 0) {
      const timer = setTimeout(() => setCooldown(cooldown - 1), 1000);
      return () => clearTimeout(timer);
    }
  }, [cooldown]);

  const handleOtpChange = (value: string, index: number) => {
    const val = value.replace(/\D/g, "");
    const newOtpValues = [...otpValues];

    if (val.length > 1) {
      const pastedData = val.slice(0, 6).split("");
      const updatedValues = new Array(6).fill("");
      pastedData.forEach((char, i) => {
        if (i < 6) updatedValues[i] = char;
      });
      setOtpValues(updatedValues);
      setOtp(updatedValues.join(""));
      document
        .getElementById(`otp-input-${Math.min(pastedData.length, 5)}`)
        ?.focus();
      return;
    }

    newOtpValues[index] = val;
    setOtpValues(newOtpValues);
    setOtp(newOtpValues.join(""));

    if (val && index < 5) {
      document.getElementById(`otp-input-${index + 1}`)?.focus();
    }
  };

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>,
    index: number,
  ) => {
    if (e.key === "Backspace" && !otpValues[index] && index > 0) {
      document.getElementById(`otp-input-${index - 1}`)?.focus();
    }
  };

  const onVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!otp || otp.length !== 6) {
      toaster.error({
        title: "Invalid OTP",
        description: "Please enter a 6-digit verification code.",
      });
      return;
    }

    try {
      setSubmitting(true);
      await AxiosInstance.post(ServerRoutes.auth.verifyEmail(), {
        email,
        otp,
      });
      toaster.success({
        title: "Email verified",
        description:
          "Your email has been verified successfully. You can now log in.",
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
    } catch (err) {
      const error = err as AxiosError<{ error?: string }>;
      toaster.error({
        title: "Verification failed",
        description:
          error.response?.data?.error ||
          "Invalid or expired verification code.",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const onResendOtp = async () => {
    try {
      setResending(true);
      await AxiosInstance.post(ServerRoutes.auth.resendOtp(), { email });
      toaster.success({
        title: "Code sent",
        description: "Check your inbox for a new verification code.",
      });
      setCooldown(60);
    } catch (err) {
      const error = err as AxiosError<{ error?: string }>;
      const errorMessage =
        error.response?.data?.error || "Failed to resend verification code.";
      toaster.error({
        title: "Unable to resend",
        description: errorMessage,
      });
      if (error.response?.status === 429) {
        setCooldown(60);
      }
    } finally {
      setResending(false);
    }
  };

  return (
    <form onSubmit={onVerify} style={{ width: "100%" }}>
      <AuthFormCard
        title="Verify your email"
        description={
          <>
            Enter the 6-digit code sent to{" "}
            <Span fontWeight="bold">{email}</Span>
          </>
        }
      >
        <Stack gap={5}>
          <Fieldset.Root size="md">
            <Field.Root required>
              <HStack gap={2} justify="center" w="full">
                {otpValues.map((digit, idx) => (
                  <Input
                    key={idx}
                    id={`otp-input-${idx}`}
                    value={digit}
                    onChange={(e) => handleOtpChange(e.target.value, idx)}
                    onKeyDown={(e) => handleKeyDown(e, idx)}
                    width="48px"
                    height="56px"
                    textAlign="center"
                    fontSize="xl"
                    fontWeight="bold"
                    borderRadius="lg"
                    bg="bg.surface"
                    borderColor="border.subtle"
                    _focus={{
                      borderColor: "brand.500",
                      outline: "2px solid",
                      outlineColor: "brand.200",
                    }}
                  />
                ))}
              </HStack>
            </Field.Root>
          </Fieldset.Root>

          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            w="full"
            borderRadius="lg"
            loading={submitting}
          >
            Confirm
          </Button>

          <Text fontSize="sm" textAlign="center" color="fg.muted">
            Didn&apos;t receive the code?{" "}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              p={0}
              color="brand.600"
              fontWeight="semibold"
              disabled={cooldown > 0 || resending}
              loading={resending}
              onClick={onResendOtp}
            >
              {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
            </Button>
          </Text>

          <Text fontSize="sm" textAlign="center" color="fg.muted">
            Back to{" "}
            <Span
              color="brand.600"
              cursor="pointer"
              fontWeight="semibold"
              onClick={() =>
                navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
              }
            >
              Sign in
            </Span>
          </Text>
        </Stack>
      </AuthFormCard>
    </form>
  );
};

export default VerifyEmail;
