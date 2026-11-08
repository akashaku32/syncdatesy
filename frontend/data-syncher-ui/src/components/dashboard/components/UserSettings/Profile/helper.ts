export type FormState = {
  firstName: string;
  lastName: string;
  company_email: string;
  cmp_name: string;
  valid_from: string;
  valid_to: string;
};

export const initialState: FormState = {
  firstName: "",
  lastName: "",
  company_email: "",
  cmp_name: "",
  valid_from: "",
  valid_to: "",
};
