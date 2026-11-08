import { StrictMode, Suspense } from "react";

import { ChakraProvider, Flex } from "@chakra-ui/react";

import { ErrorBoundary } from "react-error-boundary";
import { RouterProvider } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import { Toaster } from "@/components/ui/toaster";
import { AuthProvider } from "@/context/Auth/AuthContext";
import "@/css/index.scss";
import { router } from "@/routes/AppRoutes";
import theme from "@/theme/theme";

import "./main.scss";

import { queryClient } from "./lib/react-query-client";
import SomethingWentWrong from "./shared/SomethingWentWrong";
import { QueryClientProvider } from "@tanstack/react-query";
// import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { createRoot } from "react-dom/client";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary fallback={<SomethingWentWrong />}>
      <ChakraProvider value={theme}>
        <Toaster />
        <AuthProvider>
          <QueryClientProvider client={queryClient}>
            {/* <ReactQueryDevtools initialIsOpen={false} /> */}
            <Suspense
              fallback={
                <Flex justify="center" align="center" height="100vh">
                  <LoadingSpinner />
                </Flex>
              }
            >
              <RouterProvider router={router} />
            </Suspense>
          </QueryClientProvider>
        </AuthProvider>
      </ChakraProvider>
    </ErrorBoundary>
  </StrictMode>,
);
