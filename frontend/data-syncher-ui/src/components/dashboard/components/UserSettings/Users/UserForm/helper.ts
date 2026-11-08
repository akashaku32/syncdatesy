import ClientRoutes from "@/constants/client-routes";

const UserHomePage = {
  label: "Users",
  route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.USERS}`,
};

const BreadcrumbsForNewUser = [UserHomePage, { label: "Add member" }];

const BreadcrumbsForEditUser = [UserHomePage, { label: "Edit member" }];

export { BreadcrumbsForNewUser, BreadcrumbsForEditUser };
