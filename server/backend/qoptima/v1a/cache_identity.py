"""Causal, configuration-complete V1A training cache identity and publication."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import tempfile
import numpy as np
import pandas as pd

SCHEMA='cqc.v1a.training_cache.v2'


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()


def _numeric_identity(values):
    a=np.asarray(values,dtype='<f8',order='C')
    if np.isinf(a).any():
        raise ValueError('Infinite V1A training input')
    missing=np.isnan(a)
    clean=np.where(missing,0.,a).astype('<f8',copy=False)
    return {'shape':list(a.shape),'dtype':'little_endian_float64',
            'finite_values_sha256':hashlib.sha256(clean.tobytes(order='C')).hexdigest(),
            'missing_mask_sha256':hashlib.sha256(missing.astype(np.uint8).tobytes(order='C')).hexdigest()}


def training_source_hashes():
    here=Path(__file__).resolve().parent
    paths=[here/n for n in ['cache_identity.py','config.py','features.py','model.py','prototypes.py','training.py']]
    paths.append(here.parent/'preset_backtest.py')
    return {str(p.relative_to(here.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def execution_identity():
    versions={}
    for name in ['numpy','pandas','torch','scipy','scikit-learn']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    import platform
    import torch
    return {'versions':versions,'python':platform.python_version(),'machine':platform.machine(),
            'threads':{k:os.environ.get(k) for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']},
            'torch_threads':torch.get_num_threads(),'torch_deterministic_algorithms':torch.are_deterministic_algorithms_enabled()}


def fold_training_identity(features,targets,*,config,information_cutoff,target_maturities,
                           source_hashes=None,execution=None,signed_control=False):
    f=pd.DataFrame(features);y=pd.Series(targets).reindex(f.index);end=pd.Series(target_maturities).reindex(f.index)
    cutoff=pd.to_datetime(information_cutoff,utc=True)
    if not f.index.is_unique or not f.index.is_monotonic_increasing or (pd.to_datetime(f.index,utc=True)>cutoff).any():
        raise ValueError('V1A cache features must be chronological and admitted by fold cutoff')
    admitted_maturities=[]
    for value,maturity in zip(y.to_numpy(),end.to_numpy()):
        if pd.isna(value):
            admitted_maturities.append(None)
            continue
        if pd.isna(maturity) or pd.to_datetime(maturity,utc=True)>cutoff:
            raise ValueError('Unmatured target must be masked before cache identity')
        admitted_maturities.append(pd.Timestamp(maturity).isoformat())
    payload={'schema':SCHEMA,'config':dict(config),'signed_control':bool(signed_control),
        'information_cutoff':pd.Timestamp(information_cutoff).isoformat(),
        'feature_columns':[str(c) for c in f.columns],'dated_index':[pd.Timestamp(t).isoformat() for t in f.index],
        'features':_numeric_identity(f.to_numpy()),'masked_targets':_numeric_identity(y.to_numpy()),
        'admitted_target_maturities':admitted_maturities,
        'source_hashes':training_source_hashes() if source_hashes is None else dict(source_hashes),
        'execution':execution_identity() if execution is None else dict(execution)}
    return {'identity_sha256':_hash(payload),'payload':payload}


def metadata_training_contract_matches(metadata,identity):
    payload=identity.get('payload') or {}
    if metadata.get('config')!=payload.get('config'):
        return False
    if metadata.get('feature_names')!=payload.get('feature_columns'):
        return False
    if metadata.get('signed_control') is not payload.get('signed_control'):
        return False
    cutoff=metadata.get('artifact_information_cutoff')
    if not isinstance(cutoff,str) or not cutoff:
        return False
    try:
        time=pd.to_datetime(cutoff,utc=True)
        admitted=pd.to_datetime(payload.get('information_cutoff'),utc=True)
        if pd.isna(time) or pd.isna(admitted) or time>admitted:return False
    except (TypeError,ValueError,OverflowError):
        return False
    return True


def cache_metadata_matches(metadata,identity):
    from .training import _json_hash
    if not metadata_training_contract_matches(metadata,identity):
        return False
    if metadata.get('training_cache_identity')!=identity:
        return False
    supplied=metadata.get('spec_hash')
    if not supplied:
        return False
    base={k:v for k,v in metadata.items() if k!='spec_hash'}
    return supplied==_json_hash(base)


@contextmanager
def cache_artifact_lock(path):
    import fcntl
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def publish_cache_artifact(result,path,identity,*,save,load):
    """Caller owns same-key lock. Validate temp bytes before atomic replacement."""
    from .training import _json_hash
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    metadata=result['metadata']
    if not metadata_training_contract_matches(metadata,identity):
        raise ValueError('Training result metadata conflicts with requested cache identity')
    metadata['training_cache_identity']=identity
    metadata.pop('spec_hash',None);metadata['spec_hash']=_json_hash(metadata)
    fd,temp=tempfile.mkstemp(prefix='.'+path.stem+'.',suffix='.pt',dir=path.parent)
    os.close(fd)
    try:
        save(result,Path(temp))
        _,readback,_,_=load(Path(temp))
        if not cache_metadata_matches(readback,identity):
            raise ValueError('New V1A artifact failed cache-identity/spec-hash readback')
        with open(temp,'rb') as handle:os.fsync(handle.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temp):os.unlink(temp)
