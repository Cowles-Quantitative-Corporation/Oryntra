"""Alternative V1A tail head with simple-return support and exact warm starts.

The lower alpha quantile may be positive. FZ0 assumes negative ES; cases with
nonnegative conditional ES are outside this model's declared admissible class.
The cap uses min(v,0), continuous with an autodiff subgradient at zero.
"""
import numpy as np
import torch
import torch.nn.functional as F
from backend.qoptima.v1a.model import V1ANet

class SupportedV1ANet(V1ANet):
    def predictions_from_state(self,state,*,obs_mean=None,obs_std=None):
        mean=self.obs_mean if obs_mean is None else obs_mean
        std=self.obs_std if obs_std is None else obs_std
        z=(state['observables']-mean)/torch.clamp(std,min=1e-8)
        sv=self.var_head(z).squeeze(-1);se=self.es_head(z).squeeze(-1)
        # Float64 and clamping keep all outputs strictly inside the ES domain.
        eps=1e-9
        v=-1+F.softplus(sv)+eps
        upper=torch.minimum(v,torch.zeros_like(v))
        position=torch.clamp(torch.sigmoid(se),min=eps,max=1-eps)
        e=-1+position*(upper+1)
        e=torch.minimum(e,upper-eps)
        return v,e,z

def inverse_softplus(x):
    x=np.asarray(x,float)
    if np.any(x<=0):raise ValueError('Positive softplus target required')
    return np.where(x>30,x,np.log(np.expm1(x)))

def initialize_heads(model,labels,*,supported):
    labels=np.asarray(labels,float)
    if not np.isfinite(labels).all() or len(labels)<100 or np.any(labels < -1):
        raise ValueError('At least100 finite supported training labels required')
    alpha=float(model.config.tail_alpha)
    var=float(np.quantile(labels,alpha));es=float(labels[labels<=var].mean())
    if not (-1 < es < min(var,0)):
        raise ValueError('Training labels outside strictly negative ES model class')
    if supported:
        vb=float(inverse_softplus(1+var-1e-9));p=(1+es)/(1+min(var,0));eb=float(np.log(p/(1-p)))
    else:
        if var>=-float(model.config.var_epsilon):raise ValueError('Legacy head cannot represent nonnegative VaR')
        vb=float(inverse_softplus(-var-float(model.config.var_epsilon)));eb=float(inverse_softplus(var-es))
    with torch.no_grad():
        model.var_head.weight.zero_();model.es_head.weight.zero_()
        model.var_head.bias.fill_(vb);model.es_head.bias.fill_(eb)
    return {'method':'training_only_empirical_quantile_and_tail_mean','observations':len(labels),'var':var,'es':es,'supported':supported}
