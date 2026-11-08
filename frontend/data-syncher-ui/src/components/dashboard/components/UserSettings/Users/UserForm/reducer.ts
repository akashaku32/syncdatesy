export type UserState = {
  companyEmail: string;
  companyName: string;
  firstName: string;
  lastName: string;
  password: string;
  confirmPassword: string;
  role: string;
};

export const initialState: UserState = {
  companyEmail: "",
  companyName: "",
  firstName: "",
  lastName: "",
  password: "",
  confirmPassword: "",
  role: "",
};

export type UserAction =
  | { type: "UPDATE_FIELD"; field: keyof UserState; value: string }
  | { type: "SET_USER"; payload: UserState }
  | { type: "RESET_USER" };

export const userReducer = (
  state: UserState,
  action: UserAction,
): UserState => {
  switch (action.type) {
    case "UPDATE_FIELD":
      return {
        ...state,
        [action.field]: action.value,
      };
    case "SET_USER":
      return { ...state, ...action.payload };
    case "RESET_USER":
      return initialState;
    default:
      return state;
  }
};
