from typing import Tuple
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.distributions as D
from torch import Tensor

from modules_tools import *


class RSSMCore(nn.Module):

    def __init__(self, embed_dim=256, action_dim=7, deter_dim=200, stoch_dim=30, hidden_dim=200, global_dim=30, min_std=0.1):
        super().__init__()
        self._cell = RSSMCell(embed_dim, action_dim, deter_dim, stoch_dim, hidden_dim, global_dim, min_std)

    def forward(self,
                embed,       # tensor(N, B, E)
                action,      # tensor(N, B, A)
                reset,       # tensor(N, B)
                in_state,    # tensor(   B, D+S+G)
                glob_state,  # tensor(   B, G)
                ):

        n = embed.size(0)
        priors = []
        posts = []
        states = []
        state = in_state

        for i in range(n):
            prior, post, state = self._cell(embed[i], action[i], reset[i], state, glob_state)
            priors.append(prior)
            posts.append(post)
            states.append(state)

        return (
            torch.stack(priors),         # tensor(N, B, 2*S)
            torch.stack(posts),          # tensor(N, B, 2*S)
            torch.stack(states),         # tensor(N, B, D+S+G)
        )

    def init_state(self, batch_size):
        return self._cell.init_state(batch_size)


class RSSMCell(nn.Module):

    def __init__(self, embed_dim, action_dim, deter_dim, stoch_dim, hidden_dim, global_dim, min_std):
        super().__init__()
        self._stoch_dim = stoch_dim
        self._deter_dim = deter_dim
        self._global_dim = global_dim
        self._min_std = min_std

        self._z_mlp = nn.Linear(stoch_dim, hidden_dim)
        self._a_mlp = nn.Linear(action_dim, hidden_dim, bias=False)  # No bias, because outputs are added
        # self._g_mlp = nn.Linear(global_dim, hidden_dim, bias=False)  # TODO

        self._gru = nn.GRUCell(hidden_dim, deter_dim)

        self._prior_mlp_h = nn.Linear(deter_dim, hidden_dim)
        # self._prior_mlp_g = nn.Linear(global_dim, hidden_dim, bias=False)  # TODO
        self._prior_mlp = nn.Linear(hidden_dim, 2 * stoch_dim)

        self._post_mlp_h = nn.Linear(deter_dim, hidden_dim)
        # self._post_mlp_g = nn.Linear(global_dim, hidden_dim, bias=False)  # TODO
        self._post_mlp_e = nn.Linear(embed_dim, hidden_dim, bias=False)
        self._post_mlp = nn.Linear(hidden_dim, 2 * stoch_dim)

    def init_state(self, batch_size):
        device = next(self._gru.parameters()).device
        return torch.zeros((batch_size, self._deter_dim + self._stoch_dim + self._global_dim), device=device)

    def forward(self,
                embed,     # tensor(B, E)
                action,    # tensor(B, A)
                reset,     # tensor(B)
                in_state: Tuple[Tensor, Tensor, Tensor],  # tensor(B, D+S+G)
                glob_state,   # tensor(B, G)
                ):

        in_state = in_state * ~reset.unsqueeze(1)  # TODO perf: don't do here what you can do outside loop
        in_h, in_z, _ = split(in_state, [self._deter_dim, self._stoch_dim, self._global_dim])

        za = F.elu(self._z_mlp(in_z) + self._a_mlp(action))    # (B, H)

        h = self._gru(za, in_h)                                             # (B, D)

        prior_in = F.elu(self._prior_mlp_h(h))
        prior = to_mean_std(self._prior_mlp(prior_in), self._min_std)     # (B, 2*S)  # TODO perf: move prior outside?

        post_in = F.elu(self._post_mlp_h(h) + self._post_mlp_e(embed))
        post = to_mean_std(self._post_mlp(post_in), self._min_std)        # (B, 2*S)
        sample = diag_normal(post).rsample()                              # (B, S)   # TODO perf: rsample without D.?

        return (
            prior,                        # tensor(B, 2*S)
            post,                         # tensor(B, 2*S)
            cat3(h, sample, glob_state),  # tensor(B, D+S+G)
        )
