import {
  Toaster as ChakraToaster,
  Portal,
  Spinner,
  Stack,
  Toast,
  createToaster,
} from "@chakra-ui/react";

import { IoWarningOutline } from "react-icons/io5";
import {
  MdErrorOutline,
  MdInfoOutline,
  MdOutlineCheckCircle,
} from "react-icons/md";

// eslint-disable-next-line react-refresh/only-export-components
export const toaster = createToaster({
  placement: "top-end",
  pauseOnPageIdle: true,
});

export const Toaster = () => {
  const renderIndicator = (type: string) => {
    if (type === "loading") {
      return <Spinner size="sm" color="blue.solid" />;
    }
    if (type === "success") {
      return <MdOutlineCheckCircle color="#16A34A" size={18} />;
    }
    if (type === "error") {
      return <MdErrorOutline color="#E53E3E" size={18} />;
    }
    if (type === "warning") {
      return <IoWarningOutline color="#DD6B20" size={18} />;
    }
    if (type === "info") {
      return <MdInfoOutline color="#3182CE" size={18} />;
    }
    return <MdInfoOutline color="#718096" size={18} />;
  };

  return (
    <Portal>
      <ChakraToaster toaster={toaster} insetInline={{ mdDown: "4" }}>
        {(toast) => (
          <Toast.Root width={{ md: "sm" }}>
            {renderIndicator(String(toast.type || "info"))}
            <Stack gap="1" flex="1" maxWidth="100%">
              {toast.title && <Toast.Title>{toast.title}</Toast.Title>}
              {toast.description && (
                <Toast.Description>{toast.description}</Toast.Description>
              )}
            </Stack>
            {toast.action && (
              <Toast.ActionTrigger>{toast.action.label}</Toast.ActionTrigger>
            )}
            {toast.closable && <Toast.CloseTrigger />}
          </Toast.Root>
        )}
      </ChakraToaster>
    </Portal>
  );
};
