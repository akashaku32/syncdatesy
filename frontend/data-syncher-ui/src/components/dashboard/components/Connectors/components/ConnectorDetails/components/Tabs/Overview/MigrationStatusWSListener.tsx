import useMigrationStatusWS from "@/hooks/useMigrationStatusWS";

const MigrationStatusWSListener = ({
  migrationId,
  connectionId,
}: {
  migrationId: number;
  connectionId: number;
}) => {
  useMigrationStatusWS(migrationId, connectionId);
  return null;
};

export default MigrationStatusWSListener;
