import { useEffect, useState } from "react";

import {
  Box,
  Button,
  Field,
  Fieldset,
  Flex,
  HStack,
  Image,
  Input,
  Span,
  Stack,
  Text,
} from "@chakra-ui/react";

import { AxiosError } from "axios";
import { useLocation, useNavigate, useSearchParams } from "react-router";

import Logo from "@/assets/logo.svg";
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
      // Handle paste
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

    setSubmitting(true);
    try {
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
      const error = err as AxiosError<{ error?: string; message?: string }>;
      const errorMessage =
        error.response?.data?.error ||
        error.response?.data?.message ||
        "Verification failed. Please try again.";
      toaster.error({
        title: "Verification failed",
        description: errorMessage,
      });
    } finally {
      setSubmitting(false);
    }
  };

  const onResendOtp = async () => {
    if (cooldown > 0) return;

    setResending(true);
    try {
      await AxiosInstance.post(ServerRoutes.auth.resendOtp(), { email });
      toaster.success({
        title: "OTP Resent",
        description: "A new verification code has been sent to your email.",
      });
      setCooldown(60);
    } catch (err) {
      const error = err as AxiosError<{ error?: string; message?: string }>;
      const errorMessage =
        error.response?.data?.error ||
        error.response?.data?.message ||
        "Failed to resend OTP. Please try again.";
      toaster.error({
        title: "Resend failed",
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
    <Flex direction="column" w="100%" h="100%">
      <form onSubmit={onVerify} style={{ width: "100%" }}>
        <Stack gap={6}>
          <Fieldset.Root size="md" gap={4}>
            <Stack gap={4} mb={2} align="center">
              <Box bg="brand.50" borderRadius="full" p={4} mb={2}>
                <Image
                  src={Logo}
                  alt="Logo"
                  width="40px"
                  height="40px"
                  aspectRatio={1}
                />
              </Box>
              <Fieldset.Legend
                fontSize="28px"
                fontWeight="bold"
                textAlign="center"
              >
                Verify Your Email
              </Fieldset.Legend>
              <Text
                fontSize="md"
                color="gray.600"
                textAlign="center"
                maxW="400px"
              >
                Please enter the verification code we sent to{" "}
                <Span fontWeight="bold">{email}</Span>
              </Text>
            </Stack>

            <Field.Root required>
              <HStack gap={3} justify="center" mt={4} mb={6} w="full">
                {otpValues.map((digit, idx) => (
                  <Input
                    key={idx}
                    id={`otp-input-${idx}`}
                    value={digit}
                    onChange={(e) => handleOtpChange(e.target.value, idx)}
                    onKeyDown={(e) => handleKeyDown(e, idx)}
                    width="54px"
                    height="64px"
                    textAlign="center"
                    fontSize="2xl"
                    fontWeight="bold"
                    borderRadius="lg"
                    bg="white"
                    border="1px solid"
                    borderColor="gray.200"
                    _focus={{
                      borderColor: "brand.500",
                      ring: "1px",
                      ringColor: "brand.500",
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
            height="56px"
            fontSize="lg"
            loading={submitting}
          >
            Confirm
          </Button>

          <Text fontSize="sm" textAlign="center" color="gray.500">
            Didn't receive the code?{" "}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              p={0}
              color="brand.500"
              fontWeight="semibold"
              disabled={cooldown > 0 || resending}
              loading={resending}
              onClick={onResendOtp}
            >
              {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
            </Button>
          </Text>

          <Text fontSize="sm" textAlign="center" color="gray.500">
            Back to{" "}
            <Span
              color="brand.500"
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
      </form>
    </Flex>
  );
};

export default VerifyEmail;
