import { toaster } from "@/components/ui/toaster";

import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

type Error = {
  status: string;
  message?: string;
  description?: string;
  trial_expired?: boolean;
  error?: string;
};

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: false,
      // Avoid refetch storms on remount/navigation; screens can override.
      staleTime: 30_000,
      gcTime: 5 * 60_000,
    },
  },
  queryCache: new QueryCache({
    onError: (error: unknown, query) => {
      const err = error as Error;

      if (err.status === "401") return;

      if (err.trial_expired) return;

      // Only show toast if there's a specific error message
      if (err.message || err.description) {
        toaster.error({
          title: err.message ?? "Could not load content",
          description: err.description,
        });
      }
      console.error("Query Error:", err, "Query:", query);
    },
  }),
  mutationCache: new MutationCache({
    onError: (error: unknown, _variables, _context, mutation) => {
      const err = error as Error;

      if (err.trial_expired) return;
      if (err.error === "GOOGLE_DRIVE_FOLDER_CONFIRMATION_REQUIRED") return;

      // Only show toast if there's a specific error message
      if (err.message || err.description) {
        toaster.error({
          title: err.message ?? "Could not complete action",
          description: err.description,
        });
      }
      console.error("Mutation Error:", err, "Mutation:", mutation);
    },
  }),
});
