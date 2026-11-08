import { lazy } from "react";

export const LoginPage = lazy(() => import("@/components/auth/Login/Login"));
export const RegisterPage = lazy(
  () => import("@/components/auth/Register/Register"),
);
export const RequestTrialAccessPage = lazy(
  () => import("@/components/auth/RequestTrialAccess/RequestTrialAccess"),
);
export const ForgotPassword = lazy(
  () => import("@/components/auth/ForgotPassword/ForgotPassword"),
);

export const ResetPassword = lazy(
  () => import("@/components/auth/ResetPassword/ResetPassword"),
);

export const ChangeInitialPassword = lazy(
  () => import("@/components/auth/ChangeInitialPassword/ChangeInitialPassword"),
);

export const VerifyEmailPage = lazy(
  () => import("@/components/auth/VerifyEmail/VerifyEmail"),
);
