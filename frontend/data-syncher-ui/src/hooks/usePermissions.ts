import { useMemo } from "react";

import useAuth from "@/context/Auth/useAuth";
import { Permissions } from "@/types/auth";

const usePermissions = () => {
  const { authState } = useAuth();
  const permissions = authState.user?.permissions;

  const can = useMemo(() => {
    return (permission: keyof Permissions) => {
      return permissions ? !!permissions[permission] : false;
    };
  }, [permissions]);

  return { permissions, can };
};

export default usePermissions;
