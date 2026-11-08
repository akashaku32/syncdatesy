import { lazy } from "react";

import { Navigate, createBrowserRouter } from "react-router";

import ProtectedRoute from "@/components/auth/ProtectedRoute";
import ClientRoutes from "@/constants/client-routes";
import NotFound from "@/shared/NotFound";
import RouteError from "@/shared/RouteError";

import {
  ChangeInitialPassword,
  ForgotPassword,
  LoginPage,
  RequestTrialAccessPage,
  ResetPassword,
  VerifyEmailPage,
} from "./AuthRoutes";
import {
  ConnectorDetails,
  ConnectorOverview,
  ConnectorSchema,
  ConnectorSettings,
  ConnectorUsage,
  Connectors,
  NewConnector,
  ReverseSchema,
} from "./ConnectorRoutes";
import {
  Destination,
  DestinationForm,
  NewDestination,
} from "./DestinationRoutes";
import { AuthLayout, DashboardLayout } from "./LayoutRoutes";

const DashboardIndex = lazy(
  () => import("@/components/dashboard/DashboardIndex"),
);
const ConnectorConfiguration = lazy(
  () =>
    import(
      "@/components/dashboard/components/Connectors/components/NewConnector/components/ConnectorConfiguration/ConnectorConfiguration"
    ),
);
const Plans = lazy(
  () => import("@/components/dashboard/components/Plans/Plans"),
);
const Support = lazy(
  () => import("@/components/dashboard/components/Support/Support"),
);
const Profile = lazy(
  () =>
    import("@/components/dashboard/components/UserSettings/Profile/Profile"),
);
const Users = lazy(
  () => import("@/components/dashboard/components/UserSettings/Users/Users"),
);
const UserForm = lazy(
  () =>
    import(
      "@/components/dashboard/components/UserSettings/Users/UserForm/UserForm"
    ),
);
const AccountSettings = lazy(
  () =>
    import("@/components/dashboard/components/AccountSettings/Billing/Billing"),
);

const EmailSettings = lazy(
  () => import("@/components/dashboard/components/AccountSettings/Email/Email"),
);
const AccountProfile = lazy(
  () =>
    import("@/components/dashboard/components/AccountSettings/Profile/Profile"),
);
const AgentDownloads = lazy(
  () =>
    import(
      "@/components/dashboard/components/AccountSettings/AgentDownloads/AgentDownloads"
    ),
);
const Scheduling = lazy(
  () => import("@/components/dashboard/components/Scheduling/Scheduling"),
);

// Pages
//const HomePage = lazy(() => import("@/pages/website/HomePage"));

export const router = createBrowserRouter([
  // Setup Website Routes in the below object
  {
    path: ClientRoutes.HOME,
    element: (
      <Navigate to={`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`} replace />
    ),
    //children: [{ index: true, element: <HomePage /> }],
  },
  {
    path: ClientRoutes.AUTH,
    element: <AuthLayout />,
    children: [
      {
        index: true,
        element: <Navigate to={ClientRoutes.LOGIN} replace />,
      },
      { path: ClientRoutes.LOGIN, element: <LoginPage /> },
      { path: ClientRoutes.REGISTER, element: <RequestTrialAccessPage /> },
      { path: ClientRoutes.FORGOT_PASSWORD, element: <ForgotPassword /> },
      { path: ClientRoutes.RESET_PASSWORD, element: <ResetPassword /> },
      {
        path: ClientRoutes.CHANGE_INITIAL_PASSWORD,
        element: <ChangeInitialPassword />,
      },
      { path: ClientRoutes.VERIFY_EMAIL, element: <VerifyEmailPage /> },
    ],
  },
  // 🌟 PROTECTED ROUTES
  {
    element: <ProtectedRoute />,
    errorElement: <RouteError />,
    children: [
      {
        path: ClientRoutes.DASHBOARD,
        element: <DashboardLayout />,
        children: [
          {
            index: true,
            element: <DashboardIndex />,
          },

          /** Start Connectors Routes */
          {
            path: ClientRoutes.CONNECTORS.ROOT,
            element: (
              <ProtectedRoute permission="can_view_connectors">
                <Connectors />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.ADD}/*`,
            element: (
              <ProtectedRoute permission="can_view_connectors">
                <NewConnector />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT_CONFIGURATION}/:connectionId`,
            element: <ConnectorConfiguration mode="edit" />,
          },
          {
            path: `${ClientRoutes.CONNECTORS.ROOT}/${ClientRoutes.CONNECTORS.EDIT}/:connectionId`,
            element: (
              <ProtectedRoute permission="can_view_connectors">
                <ConnectorDetails />
              </ProtectedRoute>
            ),
            children: [
              {
                index: true,
                element: (
                  <Navigate to={ClientRoutes.CONNECTORS.OVERVIEW} replace />
                ),
              },
              {
                path: ClientRoutes.CONNECTORS.OVERVIEW,
                element: <ConnectorOverview />,
              },
              {
                path: ClientRoutes.CONNECTORS.SCHEMA,
                element: <ConnectorSchema />,
              },
              {
                path: ClientRoutes.CONNECTORS.REVERSE_SCHEMA,
                element: <ReverseSchema />,
              },
              {
                path: ClientRoutes.CONNECTORS.USAGE,
                element: <ConnectorUsage />,
              },
              {
                path: ClientRoutes.CONNECTORS.SETTINGS,
                element: <ConnectorSettings />,
              },
            ],
          },
          /** End Connectors Routes */
          /** Start Destination Routes */
          {
            path: ClientRoutes.DESTINATION.ROOT,
            element: (
              <ProtectedRoute permission="can_view_destinations">
                <Destination />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.ADD}`,
            element: <NewDestination />,
          },
          {
            path: `${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.CONFIGURE}`,
            element: <DestinationForm mode="add" />,
          },
          {
            path: `${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.EDIT}/:destinationId`,
            element: <DestinationForm mode="edit" />,
          },
          /** End Destination Routes */
          {
            path: ClientRoutes.SCHEDULING,
            element: (
              <ProtectedRoute permission="can_view_connectors">
                <Scheduling />
              </ProtectedRoute>
            ),
          },
          {
            path: ClientRoutes.PLANS,
            element: (
              <ProtectedRoute permission="can_access_billing">
                <Plans />
              </ProtectedRoute>
            ),
          },
          {
            // No dedicated permission key in API; sidebar shows Support for all authenticated users.
            path: ClientRoutes.SUPPORT,
            element: <Support />,
          },
          /** Start User Settings Routes */
          {
            path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}`,
            element: (
              <ProtectedRoute permission="can_view_users">
                <Users />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}/${ClientRoutes.USER_SETTINGS.USER_ADD}`,
            element: (
              <ProtectedRoute permission="can_view_users">
                <UserForm mode="add" />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}/${ClientRoutes.USER_SETTINGS.USER_EDIT}/:userId`,
            element: (
              <ProtectedRoute permission="can_view_users">
                <UserForm mode="edit" />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.PROFILE}`,
            element: <Profile />,
          },
          /** End User Settings Routes */
          {
            path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.BILLING}`,
            element: (
              <ProtectedRoute permission="can_access_billing">
                <AccountSettings />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.EMAIL}`,
            element: (
              <ProtectedRoute permission="can_access_settings">
                <EmailSettings />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.PROFILE}`,
            element: (
              <ProtectedRoute permission="can_access_settings">
                <AccountProfile />
              </ProtectedRoute>
            ),
          },
          {
            path: `${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.AGENT_DOWNLOADS}`,
            element: (
              <ProtectedRoute permission="can_access_settings">
                <AgentDownloads />
              </ProtectedRoute>
            ),
          },
        ],
      },
    ],
  },
  {
    path: "*",
    element: <NotFound />,
  },
]);
