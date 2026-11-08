import { type NewDestinationFormState } from "@/types/destination";

export const initialState: NewDestinationFormState = {
  dst: "",
  destinationName: "",
};

export type NewDestinationFormAction =
  | {
      type: "UPDATE_FIELD";
      field: keyof NewDestinationFormState;
      value: string;
    }
  | { type: "RESET_FORM" }
  | { type: "SET_FORM"; payload: NewDestinationFormState };

export const newDestinationFormReducer = (
  state: NewDestinationFormState,
  action: NewDestinationFormAction,
): NewDestinationFormState => {
  switch (action.type) {
    case "UPDATE_FIELD":
      return {
        ...state,
        [action.field]: action.value,
      };
    case "RESET_FORM":
      return initialState;
    case "SET_FORM":
      return action.payload;
    default:
      return state;
  }
};
