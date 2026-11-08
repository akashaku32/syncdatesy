export type UserRole = {
  role_id: number;
  role_name: string;
};

export type CreateUserPayload = {
  first_name: string;
  last_name: string;
  company_email: string;
  password: string;
  confirm_password: string;
  role: string;
};

export type UpdateUserPayload = CreateUserPayload;

export type UserTableItem = {
  user_id: number;
  first_name: string;
  last_name: string;
  company_email: string;
  company_name: string;
  role: string;
};

export type User = UserTableItem;

export type UserProfile = {
  first_name: string;
  last_name: string;
  company_email: string;
  role: string;
  company: {
    cmp_name: string;
    valid_from: string;
    valid_to: string;
  };
  profile_image?: string | null;
};

export type UpdateCurrentUserPayload = {
  first_name: string;
  last_name: string;
  company_email: string;
  cmp_name: string;
  valid_from: string;
  valid_to: string;
};
