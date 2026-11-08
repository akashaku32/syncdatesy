import React from "react";

import { AuthProvider } from "./AuthContext";

type Props = {
  children: React.ReactNode;
};

const AuthProviderComponent: React.FC<Props> = ({ children }) => {
  return <AuthProvider>{children}</AuthProvider>;
};

export default AuthProviderComponent;
