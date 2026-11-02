class CancellationRequested(RuntimeError):
    pass


class CancellationToken:
    def __init__(self, cancelled: bool = False):
        self.cancelled = cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancellationRequested("run cancellation requested")

