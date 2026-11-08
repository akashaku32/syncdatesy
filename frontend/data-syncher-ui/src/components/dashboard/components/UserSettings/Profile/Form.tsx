import { Field, Fieldset, Input, Stack } from "@chakra-ui/react";

import LoadingSpinner from "@/components/shared/Spinner";

import { type FormState } from "./helper";

const ProfileForm = ({
  initialData,
  isLoading,
}: {
  initialData: FormState;
  isLoading: boolean;
}) => {
  if (isLoading) {
    return <LoadingSpinner />;
  }

  const readOnlyInputStyles = {
    bg: "gray.200",
    color: "black",
    borderColor: "gray.300",
    opacity: 1,
    cursor: "not-allowed",
  } as const;

  return (
    <Stack gap={4} maxW={{ base: "100%", md: "500px" }}>
      <Fieldset.Root size="md" gap={4}>
        <Fieldset.Content>
          {/* First name */}
          <Field.Root required>
            <Field.Label>First name</Field.Label>
            <Input
              placeholder="Enter your first name"
              value={initialData.firstName}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
          {/* Last name */}
          <Field.Root required>
            <Field.Label>Last name</Field.Label>
            <Input
              placeholder="Enter your last name"
              value={initialData.lastName}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
          {/* Email */}
          <Field.Root required>
            <Field.Label>Email</Field.Label>
            <Input
              placeholder="Enter your email"
              value={initialData.company_email}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
          {/* Company name */}
          <Field.Root required>
            <Field.Label>Company name</Field.Label>
            <Input
              placeholder="Enter your company name"
              value={initialData.cmp_name}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
          {/* Valid from */}
          <Field.Root required>
            <Field.Label>Valid from</Field.Label>
            <Input
              type="date"
              placeholder="Enter your valid from date"
              value={initialData.valid_from}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
          {/* Valid to */}
          <Field.Root required>
            <Field.Label>Valid to</Field.Label>
            <Input
              type="date"
              placeholder="Enter your valid to date"
              value={initialData.valid_to}
              readOnly
              {...readOnlyInputStyles}
            />
          </Field.Root>
        </Fieldset.Content>
      </Fieldset.Root>
    </Stack>
  );
};

export default ProfileForm;
