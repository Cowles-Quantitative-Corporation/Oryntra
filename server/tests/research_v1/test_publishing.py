import numpy as np
import pytest
from gamma_v1.publishing import supported_tail_publication

def test_identity_does_not_cancel_small_es():
    assert 1e16-(1e16-(-.5))==0
    assert supported_tail_publication(1e16,-.5,1)==-.5
    assert supported_tail_publication(1e8,-1e-9,1)==-1e-9

def test_final_support_and_domain_guards():
    for v,e,c in [(-.5,-.8,4),(-.5,-1.1,1),(-.5,0,1),(-2,-3,1),(np.inf,-.1,1),(1,np.nan,1)]:
        with pytest.raises(ValueError):supported_tail_publication(v,e,c)
