import { Flex, FlexProps, Spinner, SpinnerProps } from "@chakra-ui/react";

interface LoadingSpinnerProps {
  containerProps?: FlexProps;
  spinnerProps?: SpinnerProps;
  size?: SpinnerProps["size"];
}

const LoadingSpinner = ({
  containerProps,
  spinnerProps,
  size = "md",
  ...flexProps
}: LoadingSpinnerProps & FlexProps) => (
  <Flex
    justify="center"
    align="center"
    height="100%"
    width="100%"
    {...flexProps}
    {...containerProps}
  >
    <Spinner
      color="brand.500"
      animationDuration="0.8s"
      {...spinnerProps}
      size={size}
    />
  </Flex>
);

export default LoadingSpinner;
