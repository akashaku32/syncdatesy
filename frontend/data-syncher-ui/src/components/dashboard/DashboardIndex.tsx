import { Navigate } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";
import usePermissions from "@/hooks/usePermissions";

const DashboardIndex = () => {
  const { authState } = useAuth();
  const { can } = usePermissions();
  const user = authState.user;

  // Wait for user profile to load
  if (!user) {
    return <LoadingSpinner />;
  }

  // Priority 1: Connectors (Admin & Analysts usually have this)
  if (can("can_view_connectors")) {
    return <Navigate to={ClientRoutes.CONNECTORS.ROOT} replace />;
  }

  // Priority 2: Billing (Billing role usually has this)
  if (can("can_access_billing")) {
    return (
      <Navigate
        to={`${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.BILLING}`}
        replace
      />
    );
  }

  // Priority 3: Default to Profile (All logged-in users)
  return (
    <Navigate
      to={`${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.PROFILE}`}
      replace
    />
  );
};

export default DashboardIndex;
