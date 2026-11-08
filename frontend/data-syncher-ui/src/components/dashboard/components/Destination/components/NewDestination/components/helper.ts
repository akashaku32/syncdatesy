import ClientRoutes from "@/constants/client-routes";

const BreadcrumbsForNewDestination = [
  {
    label: "Destinations",
    route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}`,
  },
  {
    label: "Add destination",
    route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.ADD}`,
  },

  { label: "Configure" },
];

const BreadcrumbsForEditDestination = [
  {
    label: "Destinations",
    route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}`,
  },
  { label: "Edit destination" },
];

export { BreadcrumbsForNewDestination, BreadcrumbsForEditDestination };
