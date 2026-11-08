import { useEffect, useReducer, useState } from "react";

import { Box, Button, Flex, Grid } from "@chakra-ui/react";

import { CiTrash } from "react-icons/ci";
import { MdRefresh } from "react-icons/md";

import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import ConnectorDocsHelperPanel from "@/components/dashboard/components/Connectors/components/NewConnector/components/ConnectorConfiguration/ConnectorDocsHelperPanel";
import DynamicForm from "@/components/dashboard/helpers/DynamicForm";
import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import useCreateDestination from "@/queryOptions/destination/useCreateDestination";
import { useFetchDestinationById } from "@/queryOptions/destination/useFetchDestinationById";
import useTriggerDestination from "@/queryOptions/destination/useTriggerDestination";
import { useUpdateDestination } from "@/queryOptions/destination/useUpdateDestination";
import useFetchFormSchema from "@/queryOptions/useFetchFormSchema";
import {
  type DatabricksSparkJobProvisionOutcome,
  type Destination,
  type DestinationSaveResponse,
} from "@/types/destination";
import { type ErrorResponseType } from "@/types/error";

import DeleteConfirmationDialog from "./DeleteConfirmationDialog";
import GoogleDriveFolderConfirmDialog from "./GoogleDriveFolderConfirmDialog";
import {
  BreadcrumbsForEditDestination,
  BreadcrumbsForNewDestination,
} from "./helper";
import { initialState, newDestinationFormReducer } from "./reducer";

const GDRIVE_FOLDER_CONFIRMATION_ERROR =
  "GOOGLE_DRIVE_FOLDER_CONFIRMATION_REQUIRED";

function showDatabricksSparkJobProvisionToast(
  response: DatabricksSparkJobProvisionOutcome,
) {
  if (!response.databricks_spark_job_provision_error) {
    if (
      response.databricks_spark_job_provision_status === "success" &&
      response.databricks_spark_job_name
    ) {
      toaster.success({
        title: "Dedicated serverless job ready",
        description: `Databricks job ${response.databricks_spark_job_name} is provisioned.`,
      });
    }
    return;
  }
  toaster.warning({
    title: "Dedicated serverless job",
    description: response.databricks_spark_job_provision_error,
  });
}

function destinationTestErrorDescription(err: unknown): string {
  if (typeof err === "string" && err.trim()) {
    return err.trim();
  }
  if (err && typeof err === "object" && "message" in err) {
    const axiosMsg = (err as { message?: unknown }).message;
    if (typeof axiosMsg === "string" && axiosMsg.includes("timeout")) {
      return (
        "Connection test timed out before Databricks finished starting. " +
        "Wait for compute to be running in Databricks, then try again."
      );
    }
  }
  if (!err || typeof err !== "object") {
    return "Could not test this destination. Check credentials and try again.";
  }
  const payload = err as ErrorResponseType & { detail?: unknown };
  const candidates = [payload.message, payload.error, payload.detail];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return "Could not test this destination. Check credentials and try again.";
}

const DestinationForm = ({ mode }: { mode: "edit" | "add" }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams<{ destinationId: string }>();
  const { destinationId, destinationName } = location.state || {};
  const [searchParams] = useSearchParams();

  const { mutate: createDestination, isPending } = useCreateDestination();
  const { data: destinationData, isPending: isFetchDestinationByIdPending } =
    useFetchDestinationById(params.destinationId || "");
  const { mutate: updateDestination, isPending: isUpdateDestinationPending } =
    useUpdateDestination({ id: params.destinationId || "" });

  const { data: formSchema, isLoading: isFormSchemaLoading } =
    useFetchFormSchema({
      type: destinationData?.dst || destinationName || "",
      source: "destinations",
    });

  const { mutate: triggerBackend, isPending: isTriggeringBackend } =
    useTriggerDestination(params.destinationId || "");

  useEffect(() => {
    // If the user navigates directly to this form
    // without choosing a destination on Add Destination, redirect
    // them back to the Add Destination page.
    if (mode === "add" && (!destinationId || !destinationName)) {
      navigate(
        `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.ADD}`,
        { replace: true },
      );
    }
  }, [destinationId, destinationName, mode, navigate]);

  useEffect(() => {
    const oauthStatus = searchParams.get("oauth_status");
    const oauthError = searchParams.get("oauth_error");

    if (oauthStatus === "error" && oauthError) {
      toaster.error({
        title: oauthError,
      });

      // Remove query params from the URL using replaceState
      const url = new URL(window.location.href);
      url.searchParams.delete("oauth_status");
      url.searchParams.delete("oauth_error");
      window.history.replaceState(
        {},
        document.title,
        url.pathname + url.search,
      );
    }
  }, [searchParams]);

  const [formState] = useReducer(newDestinationFormReducer, initialState);
  const [currentFormValues, setCurrentFormValues] = useState<
    Record<string, string>
  >({});
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  // ─── Google Drive folder-creation confirmation ────────────────────────────
  const [gDrivePendingPayload, setGDrivePendingPayload] =
    useState<Destination | null>(null);
  const [gDrivePendingFiles, setGDrivePendingFiles] = useState<Record<
    string,
    File | null
  > | null>(null);
  const [gDriveFolderName, setGDriveFolderName] = useState("");
  const [showGDriveConfirm, setShowGDriveConfirm] = useState(false);
  // ─────────────────────────────────────────────────────────────────────────

  /** Called once on first submit (create_folder_if_missing = false) and again
   *  on confirmed retry (create_folder_if_missing = true). */
  const submitDestination = (
    payload: Destination,
    files?: Record<string, File | null>,
    isConfirmedRetry = false,
  ) => {
    const basePayload: Destination = {
      ...payload,
      config_data: {
        ...payload.config_data,
        create_folder_if_missing: isConfirmedRetry,
      },
    };

    let finalPayload: Destination | FormData = basePayload;

    if (files && files["client_certificate_file"]) {
      const formData = new FormData();
      formData.append("config_data", JSON.stringify(basePayload));
      formData.append(
        "client_certificate_file",
        files["client_certificate_file"],
      );
      finalPayload = formData;
    }

    const handleGDrive409 = (err: ErrorResponseType) => {
      if (
        err.error === GDRIVE_FOLDER_CONFIRMATION_ERROR &&
        err.requires_confirmation &&
        err.folder_name
      ) {
        setGDrivePendingPayload(payload); // store the original payload (without the flag)
        setGDrivePendingFiles(files || null);
        setGDriveFolderName(err.folder_name);
        setShowGDriveConfirm(true);
        return true;
      }
      return false;
    };

    const handleDestinationError = (err: ErrorResponseType) => {
      if (handleGDrive409(err)) return;
      toaster.error({
        title: "Destination authorization failed",
        description:
          err.message ||
          err.error ||
          "Could not authorize this destination. Check credentials and try again.",
      });
    };

    if (mode === "edit") {
      updateDestination(finalPayload, {
        onSuccess: (response) => {
          const saveResponse = response;
          if (saveResponse.auth_url) {
            window.location.href = saveResponse.auth_url;
          } else {
            showDatabricksSparkJobProvisionToast(saveResponse);
            toaster.success({
              title: "Destination updated successfully",
              description: `Your ${formState.destinationName} destination has been updated.`,
            });
          }
        },
        onError: handleDestinationError,
      });
      return;
    }
    createDestination(finalPayload, {
      onSuccess: (response: DestinationSaveResponse) => {
        if (response.auth_url) {
          window.location.href = response.auth_url;
        } else {
          showDatabricksSparkJobProvisionToast(response);
          toaster.success({
            title: "Destination created successfully",
            description: `Your ${destinationName} destination has been created.`,
          });
          // Same as other destinations: after destination save, continue into
          // connection (connector) creation so the user can pick source + configure.
          navigate(
            `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.ADD}`,
          );
        }
      },
      onError: handleDestinationError,
    });
  };

  const handleFormSubmit = (
    values: Record<string, string>,
    files?: Record<string, File | null>,
  ) => {
    const payload: Destination = {
      dst: mode === "add" ? destinationName : destinationData?.dst,
      name: values["destination_name"],
      config_data: { ...values },
    };
    // First attempt — always send create_folder_if_missing: false
    submitDestination(payload, files, false);
  };

  /** Called when user clicks "Yes, create folder" in the confirmation dialog */
  const handleGDriveConfirm = () => {
    if (!gDrivePendingPayload) return;
    setShowGDriveConfirm(false);
    submitDestination(
      gDrivePendingPayload,
      gDrivePendingFiles || undefined,
      true,
    );
  };

  /** Called when user clicks "No, keep editing" */
  const handleGDriveCancel = () => {
    setShowGDriveConfirm(false);
    setGDrivePendingPayload(null);
    setGDrivePendingFiles(null);
    setGDriveFolderName("");
    toaster.error({
      title:
        "Folder was not created. Enter an existing Google Drive folder name or confirm folder creation to continue.",
    });
  };

  if (mode === "edit" && isFetchDestinationByIdPending) {
    return <LoadingSpinner />;
  }

  if (isFormSchemaLoading || !formSchema) {
    return <LoadingSpinner />;
  }

  const destinationKey =
    (mode === "add" ? destinationName : destinationData?.dst) ||
    destinationName ||
    "";

  return (
    <Box
      w="full"
      h={{ base: "auto", md: "calc(100vh - 64px)" }}
      mt={{ base: 0, md: -6 }}
      mb={{ base: 0, md: -6 }}
      mr={{ base: 0, md: -6 }}
      overflow="hidden"
    >
      <Grid
        templateColumns={{ base: "1fr", xl: "1fr 1fr" }}
        templateRows={{ base: "1fr 1fr", xl: "1fr" }}
        alignItems="stretch"
        gap={0}
        w="full"
        h="full"
      >
        <Box
          w="full"
          h="full"
          overflowY="auto"
          overscrollBehaviorY="contain"
          pt={{ base: 0, md: 6 }}
          pb={{ base: 4, md: 6 }}
          pr={{ base: 0, xl: 4 }}
        >
          <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
            <PageHeader
              breadcrumbs={
                mode === "add"
                  ? BreadcrumbsForNewDestination
                  : BreadcrumbsForEditDestination
              }
              title={
                mode === "add"
                  ? `Configure your ${formState.dst} destination`
                  : `Edit your ${formState.dst} destination`
              }
              subtitle="Follow guide to setup your destination"
            />

            <DynamicForm
              mode={mode === "add" ? "create" : "edit"}
              destinationName={
                mode === "add" ? destinationName : destinationData?.dst
              }
              config={{
                fields:
                  mode === "edit" && destinationData?.fields
                    ? destinationData.fields
                    : formSchema || [],
              }}
              onSubmit={(values, files) => {
                handleFormSubmit(values, files);
              }}
              loading={isPending || isUpdateDestinationPending}
              defaultValues={
                mode === "edit" && destinationData
                  ? (destinationData.config_data as Record<string, string>)
                  : undefined
              }
              onValuesChange={setCurrentFormValues}
              leftButtons={
                mode === "edit" && params.destinationId ? (
                  <Button
                    variant="outline"
                    colorPalette="red"
                    color="red.500"
                    onClick={() => setShowDeleteDialog(true)}
                  >
                    <CiTrash />
                    Delete
                  </Button>
                ) : undefined
              }
              rightButtons={
                mode === "edit" && params.destinationId ? (
                  <Button
                    variant="outline"
                    colorPalette="brand"
                    color="brand.500"
                    loading={isTriggeringBackend}
                    disabled={isTriggeringBackend}
                    loadingText={
                      destinationData?.dst === "Databricks"
                        ? "Testing (up to 15 min)…"
                        : "Testing…"
                    }
                    onClick={() =>
                      triggerBackend(
                        {
                          config_data:
                            Object.keys(currentFormValues).length > 0
                              ? currentFormValues
                              : (destinationData?.config_data ?? {}),
                        },
                        {
                          onSuccess: (response: {
                            auth_url?: string;
                            message?: string;
                          }) => {
                            if (response.auth_url) {
                              window.location.href = response.auth_url;
                            } else if (response.message) {
                              toaster.success({
                                title: response.message,
                              });
                            }
                          },
                          onError: (err: unknown) => {
                            toaster.error({
                              title: "Destination test failed",
                              description: destinationTestErrorDescription(err),
                            });
                          },
                        },
                      )
                    }
                  >
                    <MdRefresh />
                    Test destination
                  </Button>
                ) : undefined
              }
            />

            {showDeleteDialog && params.destinationId && (
              <DeleteConfirmationDialog
                open={showDeleteDialog}
                setShowDeleteDialog={setShowDeleteDialog}
                destinationId={Number(params.destinationId)}
                onSuccess={() => {
                  navigate(
                    `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}`,
                  );
                }}
              />
            )}
          </Flex>
        </Box>

        <Box
          w="full"
          h="full"
          overflow="hidden"
          bg={{ base: "transparent", xl: "gray.50" }}
        >
          <ConnectorDocsHelperPanel
            connectorKey={destinationKey}
            kind="destination"
          />
        </Box>
      </Grid>

      {/* Google Drive folder creation confirmation dialog */}
      <GoogleDriveFolderConfirmDialog
        open={showGDriveConfirm}
        folderName={gDriveFolderName}
        isLoading={isPending || isUpdateDestinationPending}
        onConfirm={handleGDriveConfirm}
        onCancel={handleGDriveCancel}
      />
    </Box>
  );
};

export default DestinationForm;
