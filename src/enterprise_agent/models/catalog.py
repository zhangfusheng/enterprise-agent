"""Load vendor model cards from ``configs/model/<vendor>/*.yaml``.

Chat construction keeps each model class's own context window. An embedding
client should override ``context_size`` from the matched card.
"""

import logging
from pathlib import Path

from pydantic import BaseModel

from enterprise_agent.models.cards import ModelCard
from enterprise_agent.models.parameters import parameters_class

logger = logging.getLogger(__name__)


class ModelCatalog:
    """Read model cards for one vendor directory at a time."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_models(
        self,
        vendor: str,
        *,
        custom_yaml_dir: Path | None = None,
    ) -> list[ModelCard]:
        """Return cards for ``vendor``, sorted by model name.

        Only ``*.yaml`` files in that vendor directory are read. ``custom_yaml_dir``
        replaces the directory. A file that fails to parse is logged and skipped.
        """
        try:
            parameters_cls = parameters_class(vendor)
        except KeyError:
            logger.warning("skipped model cards for unknown vendor %s", vendor)
            return []
        directory = self.root / vendor if custom_yaml_dir is None else custom_yaml_dir
        if not directory.is_dir():
            return []
        cards: list[ModelCard] = []
        seen: set[str] = set()
        for path in sorted(directory.glob("*.yaml")):
            card = _load_card(path, vendor, parameters_cls)
            if card is None:
                continue
            if card.name in seen:
                logger.warning(
                    "skipped duplicate model card %s for vendor %s in %s",
                    card.name,
                    vendor,
                    path.name,
                )
                continue
            seen.add(card.name)
            cards.append(card)
        cards.sort(key=lambda item: item.name)
        return cards

    def get(self, vendor: str, model_name: str) -> ModelCard:
        """Return the card whose ``name`` is the API model id."""
        for card in self.list_models(vendor):
            if card.name == model_name:
                return card
        raise KeyError(f"no model card {model_name!r} for vendor {vendor}")


def _load_card(path: Path, vendor: str, parameters_cls: type[BaseModel]) -> ModelCard | None:
    try:
        return ModelCard.from_yaml(path, parameters_cls)
    except Exception as exc:
        logger.warning("skipped model card %s for vendor %s: %s", path.name, vendor, exc)
        return None
