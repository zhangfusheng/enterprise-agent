from contextlib import contextmanager
from pathlib import Path


class CheckpointFactory:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def open(self):
        if self.url.startswith("sqlite:///"):
            path = Path(self.url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
            from langgraph.checkpoint.sqlite import SqliteSaver

            with SqliteSaver.from_conn_string(str(path)) as saver:
                saver.setup()
                yield saver
            return
        if self.url.startswith("postgresql"):
            from langgraph.checkpoint.postgres import PostgresSaver

            url = self.url.replace("postgresql+psycopg://", "postgresql://", 1)
            with PostgresSaver.from_conn_string(url) as saver:
                saver.setup()
                yield saver
            return
        raise ValueError(f"unsupported checkpoint URL: {self.url}")
