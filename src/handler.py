import runpod

from src.worker import handle_job


def handler(job):
    return handle_job(job)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

