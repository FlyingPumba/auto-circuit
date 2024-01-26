#%%
from collections import defaultdict
import json
import os
from typing import Dict, Tuple
from auto_circuit.utils.custom_tqdm import tqdm
import plotly.express as px
from torch.nn.functional import layer_norm

import torch as t
import transformer_lens as tl
from torch.nn.utils import parametrizations

from word2word import Word2word

from auto_circuit.utils.misc import get_most_similar_embeddings, remove_hooks, run_prompt, get_most_similar_embeddings
#%%

model = tl.HookedTransformer.from_pretrained_no_processing("bloom-3b")
device = model.cfg.device
#%%

en2fr = Word2word("en", "fr")
en2es = Word2word("en", "es")
n_toks = model.cfg.d_vocab_out
print("n_toks:", n_toks)
en_toks, fr_toks, es_toks = [], [], []
en_strs, fr_strs, es_strs = [], [], []
for tok in range(n_toks):
    en_tok_str = model.to_string([tok])
    assert type(en_tok_str) == str
    if len(en_tok_str) < 7:
        continue
    if en_tok_str[0] != " ":
        continue
    try:
        fr_tok_str = " " + en2fr(en_tok_str[1:], n_best=1)[0]
        # es_tok_str = " " + en2es(en_tok_str[1:], n_best=1)[0]
    except:
        continue
    # if en_tok_str.lower() == fr_tok_str.lower() or en_tok_str.lower() == es_tok_str.lower():
    if en_tok_str.lower() == fr_tok_str.lower():
    # if en_tok_str.lower() == es_tok_str.lower():
        continue
    try:
        fr_tok = model.to_single_token(fr_tok_str)
        # es_tok = model.to_single_token(es_tok_str)
    except:
        continue
    en_toks.append(tok)
    fr_toks.append(fr_tok)
    # es_toks.append(es_tok)
    en_strs.append(en_tok_str)
    fr_strs.append(fr_tok_str)
    # es_strs.append(es_tok_str)

en_toks = t.tensor(en_toks, device=device)
print(en_toks.shape)
fr_toks = t.tensor(fr_toks, device=device)
es_toks = t.tensor(es_toks, device=device)
#%%
d_model = model.cfg.d_model
en_embeds = t.nn.functional.layer_norm(model.embed.W_E[en_toks].detach().clone(), [d_model])
fr_embeds = t.nn.functional.layer_norm(model.embed.W_E[fr_toks].detach().clone(), [d_model])
# es_embeds = t.nn.functional.layer_norm(model.embed.W_E[es_toks].detach().clone(), [d_model])
# en_embeds = model.embed.W_E[en_toks].detach().clone()
# fr_embeds = model.embed.W_E[fr_toks].detach().clone()
# es_embeds = model.embed.W_E[es_toks].detach().clone()

# dataset = t.utils.data.TensorDataset(en_embeds, fr_embeds, es_embeds)
dataset = t.utils.data.TensorDataset(en_embeds, fr_embeds)
# dataset = t.utils.data.TensorDataset(en_embeds, es_embeds)
train_set, test_set = t.utils.data.random_split(dataset, [0.9, 0.1])
train_loader = t.utils.data.DataLoader(train_set, batch_size=512, shuffle=True)
test_loader = t.utils.data.DataLoader(test_set, batch_size=512, shuffle=True)

#%%

# translate = t.zeros([d_model], device=device, requires_grad=True)
# translate_2 = t.zeros([d_model], device=device, requires_grad=True)
learned_rotation = t.nn.Linear(d_model, d_model, bias=False, device=device)
linear_map = t.nn.utils.parametrizations.orthogonal(learned_rotation, "weight")
# optim = t.optim.Adam(list(linear_map.parameters()) + [translate], lr=0.01)
optim = t.optim.Adam(list(linear_map.parameters()), lr=0.0002)
# optim = t.optim.Adam(list(learned_rotation.parameters()), lr=0.01)
# optim = t.optim.Adam([translate], lr=0.01)

def word_pred_from_embeds(embeds: t.Tensor, lerp: float = 1.0) -> t.Tensor:
    # return learned_rotation(embeds + translate) - translate
    return learned_rotation(embeds)
    # return embeds + translate

def word_distance_metric(a: t.Tensor, b: t.Tensor) -> t.Tensor:
    return -t.nn.functional.cosine_similarity(a, b)
    # return (a - b) ** 2

n_epochs = 200
loss_history = []
for epoch in (epoch_pbar:=tqdm(range(n_epochs))):
    for batch_idx, (en_embed, fr_embed) in enumerate(train_loader):
        en_embed.to(device)
        fr_embed.to(device)

        optim.zero_grad()
        pred = word_pred_from_embeds(en_embed)
        loss = word_distance_metric(pred, fr_embed).mean()
        loss_history.append(loss.item())
        loss.backward()
        optim.step()
        epoch_pbar.set_description(f"Loss: {loss.item():.3f}")

px.line(y=loss_history, title="Loss History").show()
# %%
cosine_sims = []
for batch_idx, (en_embed, fr_embed) in enumerate(test_loader):
    en_embed.to(device)
    fr_embed.to(device)
    pred = word_pred_from_embeds(en_embed)
    cosine_sim = word_distance_metric(pred, fr_embed)
    cosine_sims.append(cosine_sim)

print("Test Accuracy:", t.cat(cosine_sims).mean().item())

for batch_idx, (en_embed, fr_embed) in enumerate(test_loader):
    en_embed.to(device)
    fr_embed.to(device)
    pred = word_pred_from_embeds(en_embed)
    for i in range(30):
        print()
        print()
        get_most_similar_embeddings(model, en_embed[i], top_k=4, apply_ln_final=False, apply_unembed=False, apply_embed=True)
        print()
        get_most_similar_embeddings(model, fr_embed[i], top_k=4, apply_ln_final=False, apply_unembed=False, apply_embed=True)
        print()
        get_most_similar_embeddings(model, pred[i], top_k=4, apply_ln_final=False, apply_unembed=False, apply_embed=True)
    break
# %%
#  -------------- GATHER FR EN EMBED DATA ----------------
en_file = "/home/dev/europarl/europarl-v7.fr-en.en"
fr_file = "/home/dev/europarl/europarl-v7.fr-en.fr"
batch_size = 2

en_strs = []
fr_strs = []
# Read the first 10000 lines of the files
with open(en_file, "r") as f:
    en_strs = [f.readline()[:-1] + " " + f.readline()[:-1] for _ in range(10001)][1:]
with open(fr_file, "r") as f:
    fr_strs = [f.readline()[:-1] + " " + f.readline()[:-1] for _ in range(10001)][1:]

en_toks = model.to_tokens(en_strs, prepend_bos=True, padding_side="left")
fr_toks = model.to_tokens(fr_strs, prepend_bos=True, padding_side="left")

dataset = t.utils.data.TensorDataset(en_toks, fr_toks)
train_set, test_set = t.utils.data.random_split(dataset, [0.9, 0.1])
train_loader = t.utils.data.DataLoader(train_set, batch_size=batch_size, shuffle=True)
test_loader = t.utils.data.DataLoader(test_set, batch_size=batch_size, shuffle=True)

#%%
en_embeds, fr_embeds = defaultdict(list), defaultdict(list)
lyrs = range(0, model.cfg.n_layers, 5)
for en_batch, fr_batch in tqdm(test_loader):
    with t.inference_mode():
        _, en_cache = model.run_with_cache(en_batch, prepend_bos=True)
        [en_embeds[lyr].append(en_cache[f"blocks.{lyr}.hook_resid_pre"][:, -1].detach().clone().cpu()) for lyr in lyrs]
        del en_cache
        _, fr_cache = model.run_with_cache(fr_batch, prepend_bos=True)
        [fr_embeds[lyr].append(fr_cache[f"blocks.{lyr}.hook_resid_pre"][:, -1].detach().clone().cpu()) for lyr in lyrs]
        del fr_cache
# %%
test_en_embeds = {lyr: t.cat(en_embeds[lyr]) for lyr in lyrs}
test_fr_embeds = {lyr: t.cat(fr_embeds[lyr]) for lyr in lyrs}
# %%
from auto_circuit.utils.misc import repo_path_to_abs_path
cache_folder = repo_path_to_abs_path(".activation_cache")
filename_root = f"europarl_v7_fr_en_double_prompt_final_tok-{model.cfg.model_name}-lyrs_{lyrs}"
# Save test_en_embeds and test_fr_embeds to cache with torch.save
t.save(test_en_embeds, cache_folder / f"{filename_root}-test-en.pt")
t.save(test_fr_embeds, cache_folder / f"{filename_root}-test-fr.pt")
# %%
# -------------- TRAIN FR EN EMBED ROTATION ----------------
train_en_resids = t.load("/home/dev/auto-circuit/.activation_cache/europarl_v7_fr_en_double_prompt_final_tok-bloom-3b-lyrs_range(0, 30, 5)-train-en.pt")
train_fr_resids = t.load("/home/dev/auto-circuit/.activation_cache/europarl_v7_fr_en_double_prompt_final_tok-bloom-3b-lyrs_range(0, 30, 5)-train-fr.pt")
test_en_resids = t.load("/home/dev/auto-circuit/.activation_cache/europarl_v7_fr_en_double_prompt_final_tok-bloom-3b-lyrs_range(0, 30, 5)-test-en.pt")
test_fr_resids = t.load("/home/dev/auto-circuit/.activation_cache/europarl_v7_fr_en_double_prompt_final_tok-bloom-3b-lyrs_range(0, 30, 5)-test-fr.pt")
layer_idx = 25
d_model = model.cfg.d_model
device = model.cfg.device

train_dataset = t.utils.data.TensorDataset(
    # layer_norm(train_en_resids[25], [d_model]),
    # layer_norm(train_fr_resids[25], [d_model]),
    train_en_resids[25],
    train_fr_resids[25],
)
test_dataset = t.utils.data.TensorDataset(
    # layer_norm(test_en_resids[25], [d_model]),
    # layer_norm(test_fr_resids[25], [d_model]),
    test_en_resids[25],
    test_fr_resids[25],
)
train_loader = t.utils.data.DataLoader(train_dataset, batch_size=512, shuffle=True)
test_loader = t.utils.data.DataLoader(test_dataset, batch_size=512, shuffle=True)

#%%

translate = t.zeros([d_model], device=device, requires_grad=True)
learned_rotation = t.nn.Linear(d_model, d_model, bias=False, device=device)
linear_map = t.nn.utils.parametrizations.orthogonal(learned_rotation, "weight")
optim = t.optim.Adam(list(linear_map.parameters()) + [translate], lr=0.0002)
# optim = t.optim.Adam(list(linear_map.parameters()), lr=0.0002)
# optim = t.optim.Adam(list(learned_rotation.parameters()) + [translate], lr=0.0002)
# optim = t.optim.Adam(list(learned_rotation.parameters()), lr=0.01)
# optim = t.optim.Adam([translate], lr=0.0002)

def pred_from_embeds(embeds: t.Tensor, lerp: float = 1.0) -> t.Tensor:
    return learned_rotation(embeds + translate) - translate
    # return learned_rotation(embeds)
    # return embeds + translate

def distance_metric(a: t.Tensor, b: t.Tensor) -> t.Tensor:
    # return -t.nn.functional.cosine_similarity(a, b)
    return (a - b) ** 2

n_epochs = 500
loss_history = []
for epoch in (epoch_pbar:=tqdm(range(n_epochs))):
    for batch_idx, (en_embed, fr_embed) in enumerate(train_loader):
        en_embed = en_embed.to(device)
        fr_embed = fr_embed.to(device)

        optim.zero_grad()
        pred = pred_from_embeds(fr_embed)
        loss = distance_metric(pred, en_embed).mean()
        loss_history.append(loss.item())
        loss.backward()
        optim.step()
        epoch_pbar.set_description(f"Loss: {loss.item():.3f}")
px.line(y=loss_history, title="Loss History").show()
# %%
en_file = "/home/dev/europarl/europarl-v7.fr-en.en"
fr_file = "/home/dev/europarl/europarl-v7.fr-en.fr"

# define a pytorch forward hook function
def steering_hook(module: t.nn.Module, input: Tuple[t.Tensor], output: t.Tensor) -> t.Tensor:
    print("Inside " + module.__class__.__name__ + " forward")
    print("input[0].shape:", input[0].shape)
    print("output.shape:", output.shape)
    prefix_toks, final_tok = input[0][:, :-1], input[0][:, -1]
    print("prefix_toks.shape:", prefix_toks.shape, "final_tok.shape:", final_tok.shape)
    layernormed_final_tok = layer_norm(final_tok, [d_model])
    rotated_final_tok = pred_from_embeds(final_tok)
    # rotated_final_tok = t.zeros_like(rotated_final_tok)
    out =  t.cat([prefix_toks, rotated_final_tok.unsqueeze(1)], dim=1)
    print("out.shape:", out.shape)
    return out


test_en_strs = []
test_fr_strs = []
# Read the first 10000 lines of the files
with open(en_file, "r") as f:
    for i in range(11001):
        test_str = f.readline()[:-1] + " " + f.readline()[:-1]
        if i > 10000:
            test_en_strs.append(test_str)
with open(fr_file, "r") as f:
    for i in range(11001):
        test_str = f.readline()[:-1] + " " + f.readline()[:-1]
        if i > 10000:
            test_fr_strs.append(test_str)
    

for idx, (test_en_str, test_fr_str) in enumerate(zip(test_en_strs, test_fr_strs)):
    print("test_en_str:", test_en_str)
    tl.utils.test_prompt(test_en_str, answer="hi", model=model)
    print("test_fr_str:", test_fr_str)
    with remove_hooks() as handles, t.inference_mode():
        handle = model.blocks[layer_idx].hook_resid_pre.register_forward_hook(steering_hook)
        handles.add(handle)
        logits = model(test_fr_str, prepend_bos=True)
        print("logits.shape:", logits.shape)
        get_most_similar_embeddings(model, logits[0, -1])
    break


# %%
