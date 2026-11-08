import { describe, expect, it } from "vitest";

import {
  applyTerminalDetailsPatch,
  applyTerminalMigrationPatches,
  clearTerminalMigrationPatch,
  isDetailedMigrationMessage,
  isTransientMigrationMessage,
  patchActivityLogForMigration,
  resolveJobLevelBannerMessage,
} from "@/helpers/activityLog";
import { ConnectorActivityLog } from "@/types/connectors";

import { QueryClient } from "@tanstack/react-query";

describe("isTransientMigrationMessage", () => {
  it("flags retry progress text", () => {
    expect(
      isTransientMigrationMessage(
        "Migration retrying (1/2): SFTP connection timed out while reaching 173.255.114.5:22.",
      ),
    ).toBe(true);
  });

  it("does not flag aggregated completion summaries", () => {
    expect(
      isDetailedMigrationMessage(
        "Migration failed in 12.34 seconds. Total rows: 0. Tables: 0/2 successful, 2 failed.",
      ),
    ).toBe(true);
  });

  it("recognizes partial completion with warnings", () => {
    expect(
      isDetailedMigrationMessage(
        "Migration completed with warnings in 10.00 seconds. Total rows: 100. Tables: 3/4 successful, 1 failed.",
      ),
    ).toBe(true);
  });
});

describe("resolveJobLevelBannerMessage", () => {
  it("shows retry text while migration is still running", () => {
    expect(
      resolveJobLevelBannerMessage(
        "in_progress",
        "Migration retrying (1/2): Tables failed: ['dirpersonname']",
      ),
    ).toBe("Migration retrying (1/2): Tables failed: ['dirpersonname']");
  });

  it("replaces stale retry text with a terminal summary", () => {
    expect(
      resolveJobLevelBannerMessage(
        "completed",
        "Migration retrying (2/2): waiting for worker capacity",
      ),
    ).toBe("Migration completed successfully");
  });
});

describe("terminal migration patches", () => {
  it("keeps terminal status across activity refetches until API catches up", () => {
    const queryClient = new QueryClient();
    const connectionId = 43;
    const migrationSessionId = 14647;
    const logs: ConnectorActivityLog[] = [
      {
        log_id: 433263,
        message: "Migration retrying (1/2): Tables failed: ['dirpersonname']",
        user_name: "Test",
        timestamp: "2026-10-05T07:00:00.000Z",
        status: "I",
        session_id: migrationSessionId,
        migration_id: migrationSessionId,
        is_clickable: true,
      },
    ];

    queryClient.setQueryData(["connectorActivity", connectionId, 2, "all"], {
      logs,
    });

    patchActivityLogForMigration(
      queryClient,
      connectionId,
      migrationSessionId,
      {
        overallStatus: "completed",
        message:
          "Migration completed successfully in 493.77 seconds. Total rows: 17,406. Tables: 13/13 successful.",
      },
    );

    const patched = applyTerminalMigrationPatches(connectionId, logs);
    expect(patched[0].status).toBe("S");
    expect(patched[0].message).toContain("Tables: 13/13 successful");

    const refetched = applyTerminalMigrationPatches(connectionId, [
      {
        ...logs[0],
        message: "Migration retrying (2/2): waiting for worker capacity",
        status: "I",
      },
    ]);
    expect(refetched[0].status).toBe("S");
    expect(refetched[0].message).toContain("Tables: 13/13 successful");

    clearTerminalMigrationPatch(connectionId, migrationSessionId);
  });

  it("keeps terminal patch after API briefly returns a terminal row", () => {
    const queryClient = new QueryClient();
    const connectionId = 43;
    const migrationSessionId = 14647;
    const logs: ConnectorActivityLog[] = [
      {
        log_id: 433263,
        message: "Migration in progress",
        user_name: "Test",
        timestamp: "2026-10-05T07:00:00.000Z",
        status: "I",
        session_id: migrationSessionId,
        migration_id: migrationSessionId,
        is_clickable: true,
      },
    ];

    patchActivityLogForMigration(
      queryClient,
      connectionId,
      migrationSessionId,
      {
        overallStatus: "completed",
        message:
          "Migration completed successfully in 493.77 seconds. Total rows: 17,406. Tables: 13/13 successful.",
      },
    );

    const terminalApi = applyTerminalMigrationPatches(connectionId, [
      {
        ...logs[0],
        status: "S",
        message:
          "Migration completed successfully in 493.77 seconds. Total rows: 17,406. Tables: 13/13 successful.",
      },
    ]);
    expect(terminalApi[0].status).toBe("S");

    const staleApi = applyTerminalMigrationPatches(connectionId, logs);
    expect(staleApi[0].status).toBe("S");
    expect(staleApi[0].message).toContain("Tables: 13/13 successful");

    clearTerminalMigrationPatch(connectionId, migrationSessionId);
  });
});

describe("applyTerminalDetailsPatch", () => {
  it("overrides stale in-progress details after websocket completion", () => {
    const connectionId = 12;
    const migrationSessionId = 9001;
    const queryClient = new QueryClient();

    patchActivityLogForMigration(
      queryClient,
      connectionId,
      migrationSessionId,
      {
        overallStatus: "completed",
        message:
          "Migration completed successfully in 10.00 seconds. Total rows: 5. Tables: 1/1 successful.",
      },
    );

    const patched = applyTerminalDetailsPatch(
      connectionId,
      migrationSessionId,
      {
        overall_status: "in_progress",
        job_level_message: "Migration in progress",
        tables: [],
      },
    );

    expect(patched?.overall_status).toBe("completed");
    expect(patched?.job_level_message).toContain("Tables: 1/1 successful");

    clearTerminalMigrationPatch(connectionId, migrationSessionId);
  });
});
