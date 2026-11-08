import { lazy } from "react";

export const Destination = lazy(
  () => import("@/components/dashboard/components/Destination/Destination"),
);
export const NewDestination = lazy(
  () =>
    import(
      "@/components/dashboard/components/Destination/components/NewDestination/NewDestination"
    ),
);
export const DestinationForm = lazy(
  () =>
    import(
      "@/components/dashboard/components/Destination/components/NewDestination/components/DestinationForm"
    ),
);
