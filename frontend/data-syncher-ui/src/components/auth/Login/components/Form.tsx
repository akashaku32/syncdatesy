import { useEffect, useRef, useState } from "react";

import {
  Button,
  Checkbox,
  Field,
  Fieldset,
  Flex,
  Input,
  Span,
} from "@chakra-ui/react";

import { AxiosError } from "axios";
import { Link, useLocation, useNavigate } from "react-router";

import { AuthFormCard } from "@/components/auth/AuthFormCard";
import { PasswordInput } from "@/components/ui/password-input";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import useAuth from "@/context/Auth/useAuth";
import AxiosInstance from "@/lib/axios/api-client";
import { type AuthenticationResponse } from "@/types/auth";

const getLoginRedirect = (redirectTo?: string) => {
  if (!redirectTo) return ClientRoutes.DASHBOARD;

  if (redirectTo === "destination-home" || redirectTo === "/destination-home") {
    return `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}`;
  }

  return redirectTo.startsWith("/") ? redirectTo : `/${redirectTo}`;
};

export default function Form() {
  const { login } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const passwordRef = useRef<HTMLInputElement>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState(
    "Invalid Username or Password",
  );
  const [email, setEmail] = useState("");
  const [rememberMe, setRememberMe] = useState(false);

  useEffect(() => {
    const rememberedEmail = localStorage.getItem("remembered_email");
    if (rememberedEmail) {
      setEmail(rememberedEmail);
      setRememberMe(true);
    }
  }, []);

  useEffect(() => {
    if (passwordRef.current) {
      passwordRef.current.value = "";
    }
  }, []);

  useEffect(() => {
    const message = (location.state as { message?: string } | null)?.message;
    if (!message) return;

    toaster.success({
      title: "Password changed successfully",
      description: message,
    });
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // Handle form submission logic here
    const formData = new FormData(event.currentTarget);
    const emailValue = formData.get("email") as string;
    const password = formData.get("password") as string;

    if (!emailValue || !password) {
      setHasError(true);
      return;
    }

    if (rememberMe) {
      localStorage.setItem("remembered_email", emailValue);
    } else {
      localStorage.removeItem("remembered_email");
    }

    const data = {
      username: emailValue,
      password: password,
    };
    try {
      setIsLoading(true);
      setHasError(false);
      setUnverifiedEmail(null);
      const { data: respData }: { data: AuthenticationResponse } =
        await AxiosInstance({
          method: "POST",
          url: ServerRoutes.auth.login(),
          data,
        });

      if (respData.password_change_required) {
        sessionStorage.setItem("passwordResetUid", respData.uid);
        sessionStorage.setItem("passwordResetToken", respData.token);
        navigate(respData.redirect_to);
        return;
      }

      await login(respData);
      navigate(getLoginRedirect(respData.redirect_to), { replace: true });
    } catch (err) {
      setIsLoading(false);
      const payload =
        err && typeof err === "object" && "response" in err
          ? (err as AxiosError<{ error?: string }>).response
          : null;
      const status = payload?.status;
      const message =
        payload?.data?.error ||
        (err && typeof err === "object" && "error" in err
          ? String((err as { error?: string }).error)
          : null);

      if (status === 403 || message?.toLowerCase().includes("verify")) {
        setUnverifiedEmail(data.username);
        setErrorMessage(
          message || "Please verify your email before logging in.",
        );
      } else {
        setHasError(true);
        setErrorMessage(message || "Invalid Username or Password");
        if (passwordRef.current) {
          passwordRef.current.value = "";
          passwordRef.current.focus();
        }
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleResendOtp = async () => {
    if (!unverifiedEmail) return;
    try {
      setIsLoading(true);
      await AxiosInstance.post(ServerRoutes.auth.resendOtp(), {
        email: unverifiedEmail,
      });
      navigate(`${ClientRoutes.AUTH}/${ClientRoutes.VERIFY_EMAIL}`, {
        state: { email: unverifiedEmail },
      });
    } catch (err) {
      const error = err as AxiosError<{ error?: string }>;
      setHasError(true);
      setErrorMessage(error.response?.data?.error || "Failed to resend OTP.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form style={{ width: "100%" }} onSubmit={handleSubmit} autoComplete="off">
      <AuthFormCard
        eyebrow="Sign in"
        title="Welcome back"
        description={
          <>
            Don&apos;t have an account?{" "}
            <Span
              color="brand.600"
              fontWeight="semibold"
              cursor="pointer"
              textDecoration="underline"
              textUnderlineOffset="2px"
              _hover={{ color: "brand.700" }}
              onClick={() =>
                navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`)
              }
            >
              Request access
            </Span>
          </>
        }
      >
        <Fieldset.Root
          size="lg"
          w="100%"
          invalid={hasError || !!unverifiedEmail}
          gap={4}
        >
          <Fieldset.Content gap={4}>
            <Field.Root required>
              <Field.Label>Work email</Field.Label>
              <Input
                name="email"
                type="email"
                size="lg"
                borderRadius="lg"
                placeholder="you@company.com"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </Field.Root>
            <Field.Root required>
              <Field.Label>Password</Field.Label>
              <PasswordInput
                name="password"
                size="lg"
                borderRadius="lg"
                placeholder="Enter your password"
                ref={passwordRef}
              />
            </Field.Root>
          </Fieldset.Content>
          <Fieldset.ErrorText>{errorMessage}</Fieldset.ErrorText>

          {unverifiedEmail ? (
            <Button
              variant="outline"
              colorPalette="brand"
              size="sm"
              onClick={handleResendOtp}
              loading={isLoading}
            >
              Resend OTP &amp; verify
            </Button>
          ) : null}

          <Flex justifyContent="space-between" alignItems="center">
            <Checkbox.Root
              colorPalette="brand"
              size="sm"
              checked={rememberMe}
              onCheckedChange={(e) => setRememberMe(!!e.checked)}
            >
              <Checkbox.HiddenInput name="rememberMe" />
              <Checkbox.Control />
              <Checkbox.Label fontSize="sm">Remember me</Checkbox.Label>
            </Checkbox.Root>
            <Link to={`${ClientRoutes.AUTH}/${ClientRoutes.FORGOT_PASSWORD}`}>
              <Span color="brand.600" fontSize="sm" fontWeight="medium">
                Forgot password?
              </Span>
            </Link>
          </Flex>
          <Button
            type="submit"
            colorPalette="brand"
            size="lg"
            w="full"
            borderRadius="lg"
            loading={isLoading}
            mt={1}
            boxShadow="0 8px 20px -6px rgba(13, 148, 136, 0.45)"
            _hover={{ boxShadow: "0 10px 24px -6px rgba(13, 148, 136, 0.5)" }}
          >
            Sign in
          </Button>
        </Fieldset.Root>
      </AuthFormCard>
    </form>
  );
}
