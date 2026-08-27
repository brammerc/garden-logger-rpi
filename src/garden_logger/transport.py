"""HTTPS telemetry transport with CA verification and finite deadlines."""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
import ssl
import urllib.error
import urllib.request


@dataclass(frozen=True)
class UploadResult:
    acknowledged: bool
    response_code: int | None
    error: str = ""


def _send_worker(sender, api_url: str, bearer_token: str, payload: str, timeout: float):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "garden-logger-rpi/1",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(
        api_url,
        data=payload.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        # A default context enforces hostname checks and the operating system CA store.
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            code = int(response.status)
            sender.send(UploadResult(200 <= code < 300, code, ""))
    except urllib.error.HTTPError as exc:
        sender.send(UploadResult(False, int(exc.code), f"HTTP {exc.code}"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        sender.send(UploadResult(False, None, str(exc)))
    finally:
        sender.close()


class HttpsTransport:
    def __init__(self, api_url: str, bearer_token: str, timeout_seconds: float):
        self.api_url = api_url
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def send(self, payload: str) -> UploadResult:
        receiver, sender = multiprocessing.Pipe(duplex=False)
        process = multiprocessing.Process(
            target=_send_worker,
            args=(
                sender,
                self.api_url,
                self.bearer_token,
                payload,
                self.timeout_seconds,
            ),
            daemon=True,
        )
        process.start()
        sender.close()
        try:
            if receiver.poll(self.timeout_seconds):
                try:
                    result = receiver.recv()
                except EOFError:
                    result = UploadResult(False, None, "upload worker exited")
                process.join(timeout=0.1)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.1)
                return result
            process.terminate()
            process.join(timeout=0.1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=0.1)
            return UploadResult(False, None, "upload deadline exceeded")
        finally:
            receiver.close()


def drain_queue(storage, transport, limit: int) -> int:
    """Attempt oldest items; stop on first failure to preserve FIFO behavior."""
    acknowledged = 0
    for item in storage.pending(limit):
        result = transport.send(item.payload)
        if not result.acknowledged:
            storage.mark_attempt_failed(item.event_id, result.error or "non-2xx response")
            break
        storage.acknowledge(item.event_id, int(result.response_code))
        acknowledged += 1
    return acknowledged
