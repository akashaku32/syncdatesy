import { useEffect } from "react";

import { useNavigate } from "react-router";

import PasswordResetForm from "@/components/auth/PasswordResetForm/PasswordResetForm";
import ClientRoutes from "@/constants/client-routes";

const PASSWORD_CHANGED_MESSAGE =
  "Password changed successfully. Please log in with your new password.";

const ChangeInitialPassword = () => {
  const navigate = useNavigate();
  const uid = sessionStorage.getItem("passwordResetUid");
  const token = sessionStorage.getItem("passwordResetToken");

  useEffect(() => {
    if (uid && token) return;

    navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`, { replace: true });
  }, [navigate, token, uid]);

  if (!uid || !token) return null;

  const handleSuccess = (redirectTo?: string) => {
    sessionStorage.removeItem("passwordResetUid");
    sessionStorage.removeItem("passwordResetToken");

    navigate(redirectTo || `${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`, {
      replace: true,
      state: { message: PASSWORD_CHANGED_MESSAGE },
    });
  };

  return (
    <PasswordResetForm
      uid={uid}
      token={token}
      title="Change your temporary password"
      submitLabel="Change password"
      onSuccess={handleSuccess}
    />
  );
};

export default ChangeInitialPassword;
