import numpy as np
import torch
from backend.qoptima.v1a.config import V1AConfig
from gamma_v1.model import SupportedV1ANet,initialize_heads

def model():return SupportedV1ANet(3,V1AConfig(),np.zeros((6,8)))

def test_support_ordering_and_positive_var_possible():
    m=model();state={'observables':torch.zeros((5,8),dtype=torch.float64)}
    for a in [-100,-10,0,10,100]:
        for b in [-100,-10,0,10,100]:
            with torch.no_grad():m.var_head.bias.fill_(a);m.es_head.bias.fill_(b)
            v,e,z=m.predictions_from_state(state)
            assert torch.all(v>-1) and torch.all(e>=-1) and torch.all(e<v) and torch.all(e<0)
    assert torch.all(v>0)

def test_empirical_start_exact_and_gradient_finite():
    m=model();y=np.linspace(-.3,.3,1000);init=initialize_heads(m,y,supported=True)
    out=m(torch.randn((7,3),dtype=torch.float64))
    assert np.allclose(out['var'].detach(),init['var'])
    assert np.allclose(out['es'].detach(),init['es'])
    loss=out['var'].sum()+out['es'].sum();loss.backward()
    assert all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)

def test_supported_artifact_rejects_legacy_gap_calibration(tmp_path):
    from backend.qoptima.v1a.config import V1A_MODEL_SCHEMA,V1A_SPEC_VERSION
    from backend.qoptima.v1a.inference import load_v1a_artifact
    import pytest
    m=model();meta={'schema':V1A_MODEL_SCHEMA,'spec_version':V1A_SPEC_VERSION,'config':m.config.as_dict(),'feature_names':['a','b','c'],'probes':np.zeros((6,8)).tolist(),'gamma_v1_head':'supported'}
    path=tmp_path/'model.pt';payload={'metadata':meta,'es_calibration':4.,'state_dict':m.state_dict()};torch.save(payload,path)
    with pytest.raises(ValueError,match='no-gap'):load_v1a_artifact(path)
    payload['es_calibration']=1.;torch.save(payload,path);loaded,_,c,_=load_v1a_artifact(path)
    assert isinstance(loaded,SupportedV1ANet) and c==1
