"""Stage-tagged errors so failures can be reported precisely."""


class UpdateError(Exception):
    """Base error carrying the pipeline stage and a human-readable reason."""

    def __init__(self, stage: str, reason: str):
        self.stage = stage
        self.reason = reason
        super().__init__(f"[{stage}] {reason}")


class ConfigError(UpdateError):
    def __init__(self, reason: str):
        super().__init__("config", reason)


class FetchError(UpdateError):
    def __init__(self, stage: str, reason: str, not_found: bool = False):
        self.not_found = not_found
        super().__init__(stage, reason)


class DownloadError(UpdateError):
    def __init__(self, reason: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__("download", reason)
