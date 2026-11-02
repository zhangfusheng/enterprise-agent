from .models import Base


def create_all(engine) -> None:
    Base.metadata.create_all(engine)

