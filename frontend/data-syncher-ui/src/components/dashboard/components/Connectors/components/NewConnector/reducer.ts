import { type ConnectorFormState } from "./type";

const STEPS = 3;

export type ConnectorFormAction =
  | { type: "NEXT_STEP" }
  | { type: "PREVIOUS_STEP" }
  | { type: "SET_STEP"; step: number }
  | { type: "SET_SOURCE"; source: string }
  | {
      type: "SET_DESTINATION";
      destination: string;
      destinationConfigId: number;
    }
  | { type: "UPDATE_CONFIGURATION"; field: string; value: string }
  | { type: "SET_CONFIGURATION"; configuration: Record<string, string> };

export const initialState: ConnectorFormState = {
  currentStep: 1,
  source: null,
  destination: null,
  destinationConfigId: null,
  configuration: {},
};

export const connectorFormReducer = (
  state: ConnectorFormState,
  action: ConnectorFormAction,
): ConnectorFormState => {
  switch (action.type) {
    case "NEXT_STEP":
      return {
        ...state,
        currentStep: Math.min(state.currentStep + 1, STEPS),
      };
    case "PREVIOUS_STEP":
      return {
        ...state,
        currentStep: Math.max(state.currentStep - 1, 1),
      };
    case "SET_STEP":
      return {
        ...state,
        currentStep: action.step,
      };
    case "SET_SOURCE":
      return {
        ...state,
        source: action.source,
      };
    case "SET_DESTINATION":
      return {
        ...state,
        destination: action.destination,
        destinationConfigId: action.destinationConfigId,
      };
    case "UPDATE_CONFIGURATION":
      return {
        ...state,
        configuration: {
          ...state.configuration,
          [action.field]: action.value,
        },
      };
    case "SET_CONFIGURATION":
      return {
        ...state,
        configuration: action.configuration,
      };
    default:
      return state;
  }
};
