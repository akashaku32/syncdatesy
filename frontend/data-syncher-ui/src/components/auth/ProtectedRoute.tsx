import { Navigate, Outlet, useLocation } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";
import usePermissions from "@/hooks/usePermissions";
import { Permissions } from "@/types/auth";

interface ProtectedRouteProps {
  permission?: keyof Permissions;
  children?: React.ReactNode;
}

const ProtectedRoute = ({ permission, children }: ProtectedRouteProps) => {
  const location = useLocation();
  const { authState } = useAuth();
  const { can } = usePermissions();

  const user = authState.user;
  const isTrialExpired = user?.is_trial_expired ?? false;

  const isOnPlansPage =
    location.pathname === `${ClientRoutes.DASHBOARD}/${ClientRoutes.PLANS}`;

  if (authState.isCheckingAuth) {
    return <LoadingSpinner />;
  }

  if (!authState.isAuthenticated || !authState.access_token) {
    return (
      <Navigate to={`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`} replace />
    );
  }

  if (!user) {
    return <LoadingSpinner />;
  }

  // Check trial expiration
  if (isTrialExpired && !isOnPlansPage) {
    return (
      <Navigate
        to={`${ClientRoutes.DASHBOARD}/${ClientRoutes.PLANS}`}
        replace
      />
    );
  }

  // If a specific permission is required, check it
  if (permission && !can(permission)) {
    return <Navigate to={ClientRoutes.DASHBOARD} replace />;
  }

  return children ? <>{children}</> : <Outlet />;
};

export default ProtectedRoute;
