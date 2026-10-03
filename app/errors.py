class RefreshError(Exception):
    """A refresh failure tied to a TUF stage."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class DownloadError(Exception):
    """A download failure; message is safe to return to clients."""

