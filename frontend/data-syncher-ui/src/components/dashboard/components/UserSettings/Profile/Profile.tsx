import { useMemo } from "react";

import { Flex } from "@chakra-ui/react";

import { format, parseISO } from "date-fns";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import { VIEW_CONFIG } from "@/constants/view-config";
import useAuth from "@/context/Auth/useAuth";

import ProfileForm from "./Form";
import { initialState } from "./helper";

const Profile = () => {
  const { authState } = useAuth();
  const userProfile = authState.user;
  const isLoading = !userProfile;

  const initialFormState = useMemo(() => {
    if (!userProfile) return initialState;

    const company = userProfile.company;
    const regex = /(\.\d{3})\d+Z$/;

    const startDateStr = company?.valid_from?.replace(regex, "$1Z");
    const endDateStr = company?.valid_to?.replace(regex, "$1Z");

    const cleanedStartDate = startDateStr ? parseISO(startDateStr) : new Date();
    const cleanedEndDate = endDateStr ? parseISO(endDateStr) : new Date();

    return {
      firstName: userProfile.first_name,
      lastName: userProfile.last_name,
      company_email: userProfile.company_email,
      cmp_name: userProfile.company?.cmp_name,
      valid_from: format(cleanedStartDate, "yyyy-MM-dd"),
      valid_to: format(cleanedEndDate, "yyyy-MM-dd"),
    };
  }, [userProfile]);

  return (
    <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={[
          {
            label: "User Settings",
            route: "",
          },
        ]}
        title="Profile"
        subtitle="You can update your personal details here"
      />
      <ProfileForm
        key={initialFormState.firstName}
        initialData={initialFormState}
        isLoading={isLoading}
      />
    </Flex>
  );
};

export default Profile;
