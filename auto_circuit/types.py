from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import torch as t

from auto_circuit.utils.misc import module_by_name


class ActType(Enum):
    """Type of activation. Used to determine network inputs and patch values."""

    CLEAN = 1
    CORRUPT = 2
    ZERO = 3
    # MEAN = 4

    def __str__(self) -> str:
        return self.name[0] + self.name[1:].lower()


class EdgeCounts(Enum):
    ALL = 1
    LOGARITHMIC = 2


TestEdges = EdgeCounts | List[int | float]


@dataclass(frozen=True)
class ExperimentType:
    input_type: ActType
    patch_type: ActType
    decrease_prune_scores: bool = True


HashableTensorIndex = Tuple[Optional[int], ...] | None | int
TensorIndex = Tuple[int | slice, ...] | int | slice


def tensor_index_to_slice(t_idx: HashableTensorIndex) -> TensorIndex:
    if t_idx is None:
        return slice(None)
    elif isinstance(t_idx, int):
        return t_idx
    return tuple([slice(None) if idx is None else idx for idx in t_idx])


@dataclass(frozen=True)
class Node:
    name: str
    module_name: str
    layer: int
    weight: Optional[str] = None
    _weight_t_idx: HashableTensorIndex = None

    def module(self, model: t.nn.Module) -> t.nn.Module:
        return module_by_name(model, self.module_name)

    @property
    def weight_t_idx(self) -> TensorIndex:
        return tensor_index_to_slice(self._weight_t_idx)

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class SrcNode(Node):
    _out_idx: HashableTensorIndex = None

    @property
    def out_idx(self) -> TensorIndex:
        return tensor_index_to_slice(self._out_idx)


@dataclass(frozen=True)
class DestNode(Node):
    _in_idx: HashableTensorIndex = None

    @property
    def in_idx(self) -> TensorIndex:
        return tensor_index_to_slice(self._in_idx)


@dataclass(frozen=True)
class Edge:
    src: SrcNode
    dest: DestNode

    @property
    def name(self) -> str:
        return f"{self.src.name}->{self.dest.name}"

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name
