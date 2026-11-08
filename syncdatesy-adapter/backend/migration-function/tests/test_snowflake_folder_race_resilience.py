from services.snowflake.as_source.to_sftp import _ensure_remote_dir


class _RaceSFTPClient:
    def __init__(self):
        self._existing = {"/", "/root", "/root/existing"}

    def chdir(self, path):
        normalized = path.rstrip("/") or "/"
        if normalized not in self._existing:
            raise FileNotFoundError(path)

    def mkdir(self, path):
        normalized = path.rstrip("/") or "/"
        # Simulate a parallel worker creating the directory first.
        self._existing.add(normalized)
        raise FileExistsError(path)


def test_ensure_remote_dir_tolerates_parallel_mkdir_race():
    sftp = _RaceSFTPClient()

    resolved = _ensure_remote_dir(sftp, "/root/existing/new-child")

    assert resolved == "/root/existing/new-child"
