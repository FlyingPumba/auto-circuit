import math
from typing import Dict, List

import plotly.graph_objects as go
import torch as t
from torch.nn.functional import log_softmax

from auto_circuit.prune_algos.prune_algos import PRUNE_ALGO_DICT, PruneAlgo
from auto_circuit.tasks import TASK_DICT, Task
from auto_circuit.types import (
    AlgoKey,
    AlgoPruneScores,
    BatchKey,
    MaskFn,
    PruneScores,
    TaskPruneScores,
)
from auto_circuit.utils.custom_tqdm import tqdm
from auto_circuit.utils.graph_utils import (
    batch_src_outs,
    mask_fn_mode,
    patch_mode,
    set_all_masks,
    train_mask_mode,
)
from auto_circuit.utils.tensor_ops import (
    multibatch_kl_div,
    prune_scores_threshold,
    sample_hard_concrete,
)


def train_same_under_knockouts(
    task_prune_scores: TaskPruneScores,
    algo_keys: List[AlgoKey],
    learning_rate: float,
    epochs: int,
    regularize_lambda: float,
) -> TaskPruneScores:
    task_completeness_scores: TaskPruneScores = {}
    for task_key, algo_prune_scores in (task_pbar := tqdm(task_prune_scores.items())):
        task = TASK_DICT[task_key]
        # if task_key != IOI_TOKEN_CIRCUIT_TASK.key:
        #     continue
        assert task.true_edge_count is not None
        true_circuit_size: int = task.true_edge_count
        task_pbar.set_description_str(f"Task: {task.name}")
        algo_completeness_scores: AlgoPruneScores = {}
        for algo_key, prune_scores in (algo_pbar := tqdm(algo_prune_scores.items())):
            # if algo_key not in algo_keys:
            #     print("skipping algo", algo_key)
            #     continue
            algo = PRUNE_ALGO_DICT[algo_key]
            algo_pbar.set_description_str(f"Algo: {algo.name}")

            same_under_knockouts: PruneScores = train_same_under_knockout_prune_scores(
                task=task,
                algo=algo,
                algo_ps=prune_scores,
                circuit_size=true_circuit_size,
                learning_rate=learning_rate,
                epochs=epochs,
                regularize_lambda=regularize_lambda,
            )
            algo_completeness_scores[algo_key] = same_under_knockouts
        task_completeness_scores[task_key] = algo_completeness_scores
    return task_completeness_scores


mask_p, left, right, temp = 0.9, -0.1, 1.1, 2 / 3
p = (mask_p - left) / (right - left)
init_mask_val = math.log(p / (1 - p))


def train_same_under_knockout_prune_scores(
    task: Task,
    algo: PruneAlgo,
    algo_ps: PruneScores,
    circuit_size: int,
    learning_rate: float,
    epochs: int,
    regularize_lambda: float,
    mask_fn: MaskFn = "hard_concrete",
) -> PruneScores:
    """
    Learn a subset of the circuit to knockout such that when the same edges are knocked
    out of the full model, the KL divergence between the circuit and the full model is
    maximized.
    """
    circuit_threshold = prune_scores_threshold(algo_ps, circuit_size)
    model = task.model
    n_target = int(circuit_size / 5)

    corrupt_src_outs: Dict[BatchKey, t.Tensor] = {}
    corrupt_src_outs = batch_src_outs(model, task.test_loader, "corrupt")

    loss_history, kl_div_history, reg_history = [], [], []
    with train_mask_mode(model) as patch_masks:
        mask_params = list(patch_masks.values())
        set_all_masks(model, val=0.0)

        # Make a boolean copy of the patch_masks that encodes the circuit
        circ_masks = [algo_ps[m].abs() >= circuit_threshold for m in patch_masks.keys()]
        actual_circuit_size = sum([mask.sum().item() for mask in circ_masks])
        print("actual_circuit_size", actual_circuit_size, "circuit_size", circuit_size)
        # assert actual_circuit_size == circuit_size

        set_all_masks(model, val=-init_mask_val)
        optim = t.optim.Adam(mask_params, lr=learning_rate)
        for epoch in (epoch_pbar := tqdm(range(epochs))):
            kl_str = kl_div_history[-1] if len(kl_div_history) > 0 else None
            epoch_pbar.set_description_str(f"Epoch: {epoch}, KL Div: {kl_str}")
            for batch in task.test_loader:
                patches = corrupt_src_outs[batch.key].clone().detach()
                with patch_mode(model, patches), mask_fn_mode(model, mask_fn):
                    optim.zero_grad()
                    model.zero_grad()

                    # Patch all the edges not in the circuit
                    with t.no_grad():
                        for circ, patch in zip(circ_masks, mask_params):
                            patch_all = t.full_like(patch.data, 99)
                            t.where(circ, patch.data, patch_all, out=patch.data)
                    model_out = model(batch.clean)[model.out_slice]
                    circuit_logprobs = log_softmax(model_out, dim=-1)

                    # Don't patch edges not in the circuit
                    with t.no_grad():
                        for cir, patch in zip(circ_masks, mask_params):
                            patch_none = t.full_like(patch.data, -99)
                            t.where(cir, patch.data, patch_none, out=patch.data)
                    model_out = model(batch.clean)[model.out_slice]
                    model_logprobs = log_softmax(model_out, dim=-1)
                    kl_div_term = -multibatch_kl_div(circuit_logprobs, model_logprobs)
                    kl_div_history.append(kl_div_term.item())

                    flat_masks = t.cat([mask.flatten() for mask in mask_params])
                    knockouts_samples = sample_hard_concrete(flat_masks, batch_size=1)
                    reg_term = t.relu(knockouts_samples.sum() - n_target) / n_target
                    reg_history.append(reg_term.item() * regularize_lambda)

                    loss = kl_div_term + reg_term * regularize_lambda
                    loss.backward()
                    loss_history.append(loss.item())
                    optim.step()

        fig = go.Figure()
        fig.add_trace(go.Scatter(y=loss_history, name="Loss"))
        fig.add_trace(go.Scatter(y=kl_div_history, name="KL Divergence"))
        fig.add_trace(go.Scatter(y=reg_history, name="Regularization"))
        fig.update_layout(
            title=f"Same Under Knockouts for Task: {task.name}, Algo: {algo.name}"
        )
        fig.show()

    completeness_prune_scores: PruneScores = {}
    for mod_name, patch_mask in model.patch_masks.items():
        completeness_prune_scores[mod_name] = patch_mask.detach().clone()
    return completeness_prune_scores
