from copy import deepcopy
from typing import Any, List, Optional

import torch as t
from transformer_lens import HookedTransformer

from auto_circuit.model_utils.task_projectors.task_projector import TaskProjector
from auto_circuit.types import AutoencoderInput
from auto_circuit.utils.patchable_model import PatchableModel
from auto_circuit.utils.tensor_ops import MaskFn


class ProjectorTransformer(t.nn.Module):
    wrapped_model: t.nn.Module
    projectors: List[TaskProjector]
    projector_layers: List[int]

    def __init__(
        self,
        wrapped_model: t.nn.Module,
        projectors: List[TaskProjector],
        layers: List[int]
    ):
        super().__init__()
        self.projectors = projectors
        self.projector_layers = layers

        if isinstance(wrapped_model, PatchableModel):
            self.wrapped_model = wrapped_model.wrapped_model
        else:
            self.wrapped_model = wrapped_model

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model(*args, **kwargs)

    def run_with_cache(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model.run_with_cache(*args, **kwargs)

    def add_hook(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model.add_hook(*args, **kwargs)

    @property
    def cfg(self) -> Any:
        return self.wrapped_model.cfg

    @property
    def tokenizer(self) -> Any:
        return self.wrapped_model.tokenizer

    @property
    def input_to_embed(self) -> Any:
        return self.wrapped_model.input_to_embed

    @property
    def blocks(self) -> Any:
        return self.wrapped_model.blocks

    def to_tokens(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model.to_tokens(*args, **kwargs)

    def to_str_tokens(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model.to_str_tokens(*args, **kwargs)

    def to_string(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped_model.to_string(*args, **kwargs)

    def __str__(self) -> str:
        return self.wrapped_model.__str__()

    def __repr__(self) -> str:
        return self.wrapped_model.__repr__()


def get_projector_model(
    model: HookedTransformer,
    projector_input: AutoencoderInput,
    mask_fn: MaskFn,
    load_pretrained: bool,
    seq_len: Optional[int] = None,
    new_instance: bool = True,
    layers: Optional[List[int]] = None,
) -> ProjectorTransformer:
    if new_instance:
        model = deepcopy(model)
    projectors: List[TaskProjector] = []
    projector_layers: List[int] = []
    for layer_idx in range(model.cfg.n_layers):
        if layers is not None and layer_idx not in layers:
            continue
        if projector_input == "mlp_post_act":
            hook_points = [model.blocks[layer_idx].mlp.hook_post]
            hook_modules = [model.blocks[layer_idx].mlp]
            hook_names = ["hook_post"]
            hook_layers = [layer_idx]
        elif projector_input == "resid":
            hook_points = [model.blocks[layer_idx].hook_resid_pre]
            hook_modules = [model.blocks[layer_idx]]
            hook_names = ["hook_resid_pre"]
            hook_layers = [layer_idx]
            if layer_idx == model.cfg.n_layers - 1:
                hook_points.append(model.blocks[layer_idx].hook_resid_post)
                hook_modules.append(model.blocks[layer_idx])
                hook_names.append("hook_resid_post")
                hook_layers.append(layer_idx + 1)
        else:
            assert projector_input == "resid_delta_mlp"
            hook_points = [model.blocks[layer_idx].hook_mlp_out]
            hook_modules = [model.blocks[layer_idx]]
            hook_names = ["hook_mlp_out"]
            hook_layers = [layer_idx]
        if load_pretrained:
            raise NotImplementedError
        for hp, mod, name in zip(hook_points, hook_modules, hook_names):
            projector = TaskProjector(hp, model.cfg.d_model, seq_len, mask_fn)
            projector.to(model.cfg.device)
            setattr(mod, name, projector)
            projectors.append(projector)
            projector_layers.append(layer_idx)

    return ProjectorTransformer(model, projectors, projector_layers)


# def factorized_src_nodes(model: ProjectorTransformer) -> Set[SrcNode]:
#     """Get the source part of each edge in the factorized graph, grouped by layer.
#     Graph is factorized following the Mathematical Framework paper."""
#     assert model.cfg.use_attn_result  # Get attention head outputs separately
#     assert (
#         model.cfg.use_attn_in
#     )  # Get attention head inputs separately (but Q, K, V are still combined)
#     assert model.cfg.use_split_qkv_input  # Separate Q, K, V input for each head
#     assert model.cfg.use_hook_mlp_in  # Get MLP input BEFORE layernorm
#     layers, idxs = count(), count()
#     nodes = set()
#     nodes.add(
#         SrcNode(
#             name="Resid Start",
#             module_name="blocks.0.hook_resid_pre",
#             layer=next(layers),
#             idx=next(idxs),
#             weight="embed.W_E",
#         )
#     )

#     for block_idx in range(model.cfg.n_layers):
#         layer = next(layers)
#         for head_idx in range(model.cfg.n_heads):
#             nodes.add(
#                 SrcNode(
#                     name=f"A{block_idx}.{head_idx}",
#                     module_name=f"blocks.{block_idx}.attn.hook_result",
#                     layer=layer,
#                     idx=next(idxs),
#                     head_dim=2,
#                     head_idx=head_idx,
#                     weight=f"blocks.{block_idx}.attn.W_O",
#                     weight_head_dim=0,
#                 )
#             )
#         layer = layer if model.cfg.parallel_attn_mlp else next(layers)
#         for latent_idx in range(model.blocks[block_idx].hook_mlp_out.n_latents):
#             nodes.add(
#                 SrcNode(
#                     name=f"MLP {block_idx} Latent {latent_idx}",
#                     module_name=f"blocks.{block_idx}.hook_mlp_out.latent_outs",
#                     layer=layer,
#                     idx=next(idxs),
#                     head_dim=2,
#                     head_idx=latent_idx,
#                     weight=f"blocks.{block_idx}.hook_mlp_out.decoder.weight",
#                     weight_head_dim=0,
#                 )
#             )
#     return nodes


# def factorized_dest_nodes(model: ProjectorTransformer) -> Set[DestNode]:
#     """Get the destination part of each edge in the factorized graph, grouped by layer
#     Graph is factorized following the Mathematical Framework paper."""
#     assert model.cfg.use_attn_result  # Get attention head outputs separately
#     assert (
#         model.cfg.use_attn_in
#     )  # Get attention head inputs separately (but Q, K, V are still combined)
#     # assert model.cfg.use_split_qkv_input  # Separate Q, K, V input for each head
#     assert model.cfg.use_hook_mlp_in  # Get MLP input BEFORE layernorm
#     layers, idxs = count(1), count()
#     nodes = set()
#     for block_idx in range(model.cfg.n_layers):
#         layer = next(layers)
#         for head_idx in range(model.cfg.n_heads):
#             nodes.add(
#                 DestNode(
#                     name=f"A{block_idx}.{head_idx}",
#                     module_name=f"blocks.{block_idx}.hook_attn_in",
#                     layer=layer,
#                     idx=next(idxs),
#                     head_dim=2,
#                     head_idx=head_idx,
#                 )
#             )
#         nodes.add(
#             DestNode(
#                 name=f"MLP {block_idx}",
#                 module_name=f"blocks.{block_idx}.hook_mlp_in",
#                 layer=layer if model.cfg.parallel_attn_mlp else next(layers),
#                 idx=next(idxs),
#                 weight=f"blocks.{block_idx}.mlp.W_in",
#             )
#         )
#     nodes.add(
#         DestNode(
#             name="Resid End",
#             module_name=f"blocks.{model.cfg.n_layers - 1}.hook_resid_post",
#             layer=next(layers),
#             idx=next(idxs),
#             weight="unembed.W_U",
#         )
#     )
#     return nodes
