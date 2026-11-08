import { useEffect, useMemo, useReducer } from "react";

import { Flex } from "@chakra-ui/react";

import { useLocation, useNavigate, useParams } from "react-router";

import ConnectorConfiguration from "./components/ConnectorConfiguration/ConnectorConfiguration";
import S3ConnectorConfiguration from "./components/ConnectorConfiguration/S3ConnectorConfiguration";
import DestinationSelection from "./components/DestinationSelection/DestinationSelection";
import SourceSelection from "./components/SourceSelection/SourceSelection";
import { connectorFormReducer, initialState } from "./reducer";

const GDRIVE_RETURN_PATH_KEY = "gdrive_return_path";
const GDRIVE_SOURCE_KEY = "gdrive_source";
const GDRIVE_DESTINATION_KEY = "gdrive_destination";
const GDRIVE_DESTINATION_CONFIG_ID_KEY = "gdrive_destination_config_id";

const getSavedGoogleDriveDestination = () => {
  try {
    return sessionStorage.getItem(GDRIVE_DESTINATION_KEY) || null;
  } catch {
    return null;
  }
};

const getSavedGoogleDriveDestinationConfigId = (): number | null => {
  try {
    const raw = sessionStorage.getItem(GDRIVE_DESTINATION_CONFIG_ID_KEY);
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
};

const getSavedGoogleDriveSource = () => {
  try {
    return sessionStorage.getItem(GDRIVE_SOURCE_KEY) || null;
  } catch {
    return null;
  }
};

const NewConnector = () => {
  const { "*": connectorPath = "" } = useParams();

  const pathParts = connectorPath.split("/").filter(Boolean);
  const source =
    pathParts[0] === "select-destination" ? pathParts[1] || null : null;
  const navigate = useNavigate();
  const location = useLocation();

  // Detect if we are returning from a Google OAuth redirect:
  // Either tokens are in the URL (access_token param), an error occurred, or we saved a return path before leaving
  const isGoogleOAuthReturn = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return (
      params.has("access_token") ||
      params.has("oauth_error") ||
      params.has("error") ||
      !!sessionStorage.getItem(GDRIVE_RETURN_PATH_KEY)
    );
  }, []);

  // On OAuth return, restore the destination name/id from sessionStorage
  const initialDestination = isGoogleOAuthReturn
    ? getSavedGoogleDriveDestination()
    : null;
  const initialDestinationConfigId = isGoogleOAuthReturn
    ? getSavedGoogleDriveDestinationConfigId()
    : null;

  // Restore or decode source from path or sessionStorage
  const initialSource = source
    ? decodeURIComponent(source)
    : isGoogleOAuthReturn
      ? getSavedGoogleDriveSource()
      : null;

  // Only jump to step 3 when both destination AND source are known
  const initialStep =
    initialDestination && initialSource ? 3 : initialDestination ? 2 : 1;

  const [state, dispatch] = useReducer(connectorFormReducer, {
    ...initialState,
    currentStep: initialStep,
    destination: initialDestination,
    destinationConfigId: initialDestinationConfigId,
    source: initialSource,
  });

  useEffect(() => {
    // Do NOT navigate away if we are returning from Google OAuth —
    // the GoogleDriveOAuth component must render on the current page to read tokens
    if (isGoogleOAuthReturn) return;

    let newPath = "/dashboard/connectors/add";

    if (state.currentStep >= 2 && state.destination) {
      newPath += "/select-destination";
      if (state.currentStep === 3 && state.source) {
        newPath += `/${encodeURIComponent(state.source)}`;
      }
    }

    if (location.pathname !== newPath) {
      navigate(newPath, { replace: true });
    }
  }, [
    isGoogleOAuthReturn,
    state.currentStep,
    state.destination,
    state.source,
    navigate,
    location.pathname,
  ]);

  const handleNext = () => dispatch({ type: "NEXT_STEP" });
  const handlePrevious = () => dispatch({ type: "PREVIOUS_STEP" });

  const handleStepClick = (stepId: number) => {
    // Allow navigation to previous steps only
    if (stepId <= state.currentStep) {
      dispatch({ type: "SET_STEP", step: stepId });
    }
  };

  const handleSourceSelect = (sourceParam: string) => {
    // Save the source type before navigating away (needed for OAuth return)
    sessionStorage.setItem(GDRIVE_SOURCE_KEY, sourceParam);
    dispatch({ type: "SET_SOURCE", source: sourceParam });
    handleNext();
  };

  const handleDestinationSelect = ({
    name,
    dst_config_id,
  }: {
    name: string;
    dst_config_id: number;
  }) => {
    // Save the destination before navigating away (needed for OAuth return)
    sessionStorage.setItem(GDRIVE_DESTINATION_KEY, name);
    sessionStorage.setItem(
      GDRIVE_DESTINATION_CONFIG_ID_KEY,
      String(dst_config_id),
    );
    dispatch({
      type: "SET_DESTINATION",
      destination: name,
      destinationConfigId: dst_config_id,
    });
    handleNext();
  };

  const isStepCompleted = (stepId: number) => {
    switch (stepId) {
      case 1:
        return !!state.destination;
      case 2:
        return !!state.source;
      case 3:
        return Object.keys(state.configuration).length > 0;
      default:
        return false;
    }
  };

  const renderStepContent = () => {
    switch (state.currentStep) {
      case 1:
        return (
          <DestinationSelection
            selectedDestination={state.destination}
            onDestinationSelect={handleDestinationSelect}
          />
        );
      case 2:
        if (!state.destination) handleStepClick(1);
        return (
          <>
            {state.destination ? (
              <SourceSelection
                selectedSource={state.source}
                onSourceSelect={handleSourceSelect}
                handlePrevious={handlePrevious}
              />
            ) : (
              <Flex>Please Choose a Destination</Flex>
            )}
          </>
        );
      case 3: {
        // On OAuth return, isStepCompleted(2) would be false because source was null
        // before our fix; skip the null check if returning from OAuth
        if (!isGoogleOAuthReturn && !isStepCompleted(2)) return null;

        // Determine if this source should use the file-based connector flow.
        const normalizedSource = state.source
          ?.toLowerCase()
          .replace(/[\s\-._]/g, "");
        const isS3Connector =
          normalizedSource === "amazons3" ||
          normalizedSource === "sftp" ||
          normalizedSource === "googledrive" ||
          normalizedSource === "azuredatalakestorage" ||
          normalizedSource === "adls";

        // Route to appropriate configuration component
        return isS3Connector ? (
          <S3ConnectorConfiguration
            state={state}
            handlePrevious={handlePrevious}
            mode="create"
          />
        ) : (
          <ConnectorConfiguration
            state={state}
            handlePrevious={handlePrevious}
            mode="create"
          />
        );
      }
      default:
        return null;
    }
  };

  return <Flex direction="column">{renderStepContent()}</Flex>;
};
export default NewConnector;
