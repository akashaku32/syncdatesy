export type LoginResponse = {
  message?: string;
  access_token: string;
  refresh_token: string;
  redirect_to?: string;
  password_change_required?: false;
  user: User | LoginUser;
};

export type LoginUser = {
  id: number;
  role: string;
  role_id: number;
  account_id: number;
};

export type InitialPasswordChangeResponse = {
  message: string;
  password_change_required: true;
  uid: string;
  token: string;
  redirect_to: string;
};

export type AuthenticationResponse =
  | LoginResponse
  | InitialPasswordChangeResponse;

export interface AuthContextType {
  login: (_arg0: LoginResponse) => Promise<void>;
  logout: () => void;
  authState: AuthState;
}

export type Permissions = {
  can_view_connectors: boolean;
  can_create_connectors: boolean;
  can_edit_connectors: boolean;
  can_delete_connectors: boolean;
  can_view_destinations: boolean;
  can_create_destinations: boolean;
  can_edit_destinations: boolean;
  can_delete_destinations: boolean;
  can_view_tables: boolean;
  can_select_tables: boolean;
  can_edit_tables: boolean;
  can_view_connection_details: boolean;
  can_edit_connection_settings: boolean;
  can_toggle_connections: boolean;
  can_update_sync_frequency: boolean;
  can_view_logs: boolean;
  can_view_metrics: boolean;
  can_view_migration_status: boolean;
  can_view_users: boolean;
  can_create_users: boolean;
  can_edit_users: boolean;
  can_delete_users: boolean;
  can_access_billing: boolean;
  can_view_billing_charts: boolean;
  can_view_billing_plans: boolean;
  can_update_billing_plan: boolean;
  can_access_settings: boolean;
  can_edit_company_settings: boolean;
};

export type User = {
  user_id: number;
  first_name: string;
  last_name: string;
  company_email: string;
  role: string;
  profile_image: string | null;
  company: {
    cmp_id: number;
    cmp_name: string;
    valid_from: string;
    valid_to: string;
  };
  is_trial: boolean;
  trial_days_remaining: number;
  is_trial_expired: boolean;
  message?: string;
  permissions: Permissions;
};

export type AuthState = {
  isAuthenticated: boolean;
  isCheckingAuth: boolean;
  user: User | null;
  access_token: string | null;
  refresh_token: string | null;
};
