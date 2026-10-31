from __future__ import annotations

from typing import Any

try:
    import torch
    from torch import nn
    import torch.nn.functional as F
except Exception as exc:  # pragma: no cover - exercised only on runtimes missing torch
    torch = None
    nn = None
    F = None
    _TORCH_ERROR = exc
else:
    _TORCH_ERROR = None

from .config import V1AConfig


def require_torch() -> None:
    if torch is None:
        raise RuntimeError(
            "V1A requires PyTorch. Install the V1A research requirements into Oryntra's private runtime."
        ) from _TORCH_ERROR


class V1ANet(nn.Module if nn is not None else object):
    def __init__(
        self,
        input_dim: int,
        config: V1AConfig,
        probes,
        *,
        signed_control: bool = False,
    ):
        require_torch()
        super().__init__()
        self.config = config
        self.signed_control = bool(signed_control)
        k = int(config.latent_states)
        r = int(config.factor_rank)
        self.trunk = nn.Sequential(
            nn.Linear(int(input_dim), 64), nn.SiLU(),
            nn.Linear(64, 32), nn.SiLU(),
        )
        self.p_head = nn.Linear(32, k)
        self.direction_head = nn.Linear(32, k * r)
        self.strength_head = nn.Linear(32, k)
        self.kappa_head = nn.Linear(32, r)
        obs_dim = int(config.probe_count) + 2
        self.var_head = nn.Linear(obs_dim, 1)
        self.es_head = nn.Linear(obs_dim, 1)
        probe_tensor = torch.as_tensor(probes, dtype=torch.float64)
        if tuple(probe_tensor.shape) != (int(config.probe_count), k):
            raise ValueError(f"expected probes shape {(config.probe_count, k)}, got {tuple(probe_tensor.shape)}")
        self.register_buffer("probes", probe_tensor)
        self.register_buffer("obs_mean", torch.zeros(obs_dim, dtype=torch.float64))
        self.register_buffer("obs_std", torch.ones(obs_dim, dtype=torch.float64))
        self.double()

    @staticmethod
    def g(s):
        return s / torch.sqrt(1.0 + s * s)

    def raw_state(self, x):
        cfg = self.config
        h = self.trunk(x)
        p0 = torch.softmax(self.p_head(h), dim=-1)
        k = int(cfg.latent_states)
        p = float(cfg.probability_floor) + (1.0 - k * float(cfg.probability_floor)) * p0

        raw_dir = self.direction_head(h).reshape(-1, k, int(cfg.factor_rank))
        norm = torch.sqrt(torch.sum(raw_dir * raw_dir, dim=-1, keepdim=True) + float(cfg.direction_epsilon))
        u = raw_dir / norm
        a = float(cfg.a_max) * torch.sigmoid(self.strength_head(h))
        L = a.unsqueeze(-1) * u

        raw_kappa = self.kappa_head(h)
        gk = self.g(raw_kappa)
        kappa = gk if self.signed_control else (1.0 + gk) / 2.0
        A = torch.einsum("bik,bk,bjk->bij", L, kappa, L)
        diag_a = torch.diagonal(A, dim1=-2, dim2=-1)
        C = A + torch.diag_embed(1.0 - diag_a)
        sqrt_p = torch.sqrt(p)
        X = sqrt_p.unsqueeze(-1) * C * sqrt_p.unsqueeze(-2)
        eig = torch.linalg.eigvalsh(X)

        # q^T X q for a fixed bank of training-fold economic probes.
        probe_obs = torch.einsum("mk,bkl,ml->bm", self.probes, X, self.probes)
        tau = max(float(cfg.softmin_temperature), 1e-8)
        soft_min = -tau * torch.logsumexp(-eig / tau, dim=-1, keepdim=True)
        off = X - torch.diag_embed(torch.diagonal(X, dim1=-2, dim2=-1))
        off_energy = torch.sum(off * off, dim=(-2, -1), keepdim=False).unsqueeze(-1)
        observables = torch.cat([probe_obs, soft_min, off_energy], dim=-1)
        return {
            "p": p,
            "L": L,
            "kappa": kappa,
            "A": A,
            "C": C,
            "X": X,
            "eigvals": eig,
            "observables": observables,
        }

    def predictions_from_state(self, state: dict[str, Any], *, obs_mean=None, obs_std=None):
        mean = self.obs_mean if obs_mean is None else obs_mean
        std = self.obs_std if obs_std is None else obs_std
        z = (state["observables"] - mean) / torch.clamp(std, min=1e-8)
        sv = self.var_head(z).squeeze(-1)
        se = self.es_head(z).squeeze(-1)
        v = -F.softplus(sv) - float(self.config.var_epsilon)
        e = v - F.softplus(se)
        return v, e, z

    def forward(self, x):
        state = self.raw_state(x)
        v, e, z = self.predictions_from_state(state)
        return {**state, "var": v, "es": e, "standardized_observables": z}

    def set_observable_stats(self, mean, std) -> None:
        with torch.no_grad():
            self.obs_mean.copy_(torch.as_tensor(mean, dtype=torch.float64, device=self.obs_mean.device))
            self.obs_std.copy_(torch.clamp(torch.as_tensor(std, dtype=torch.float64, device=self.obs_std.device), min=1e-8))




def assemble_latent_state(p, L, kappa):
    """Assemble C and X from anchored probabilities and factor coefficients.

    Kept as a separate function so the PSD/signed-control algebra can be
    property-tested independently of the neural trunk.
    """
    require_torch()
    p = torch.as_tensor(p, dtype=torch.float64)
    L = torch.as_tensor(L, dtype=torch.float64)
    kappa = torch.as_tensor(kappa, dtype=torch.float64)
    if p.ndim == 1:
        p = p.unsqueeze(0)
    if L.ndim == 2:
        L = L.unsqueeze(0)
    if kappa.ndim == 1:
        kappa = kappa.unsqueeze(0)
    A = torch.einsum("bik,bk,bjk->bij", L, kappa, L)
    C = A + torch.diag_embed(1.0 - torch.diagonal(A, dim1=-2, dim2=-1))
    sqrt_p = torch.sqrt(p)
    X = sqrt_p.unsqueeze(-1) * C * sqrt_p.unsqueeze(-2)
    return C, X

def fz0_loss(y, v, e, alpha: float):
    require_torch()
    a = float(alpha)
    indicator = (y <= v).to(dtype=y.dtype)
    return -(indicator * (v - y)) / (a * e) + v / e + torch.log(-e) - 1.0
