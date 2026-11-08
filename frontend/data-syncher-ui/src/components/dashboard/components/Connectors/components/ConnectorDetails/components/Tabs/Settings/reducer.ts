import { type ConnectorSettingsApiResponse } from "@/types/connectors";

export type SettingsState = Partial<ConnectorSettingsApiResponse>;

export type SettingsAction =
  | { type: "SET_FIELD"; field: keyof SettingsState; value: string | number }
  | { type: "RESET"; payload: SettingsState };

export function reducer(
  state: SettingsState,
  action: SettingsAction,
): SettingsState {
  switch (action.type) {
    case "SET_FIELD":
      return {
        ...state,
        [action.field]: action.value,
      };
    case "RESET":
      return { ...action.payload };
    default:
      return state;
  }
}
