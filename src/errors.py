class WorkerError(Exception):
    prefix = "ERROR"

    def __init__(self, message: str):
        super().__init__(f"{self.prefix}: {message}")
        self.message = message


class InvalidInput(WorkerError):
    prefix = "INVALID_INPUT"


class DownloadFailed(WorkerError):
    prefix = "DOWNLOAD_FAILED"


class FFprobeFailed(WorkerError):
    prefix = "FFPROBE_FAILED"


class FFmpegFailed(WorkerError):
    prefix = "FFMPEG_FAILED"


class UploadFailed(WorkerError):
    prefix = "UPLOAD_FAILED"


class LimitExceeded(WorkerError):
    prefix = "LIMIT_EXCEEDED"
