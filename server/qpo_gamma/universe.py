"""Performance-independent coverage checks, without synthetic PIT membership."""
import json,sqlite3,zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from .identity import digest,atomic_json

def coverage(prices,calendar,*,start,end,warmup,target_assets,official_tickers=None,sec_symbols=(),volumes=None):
    if not prices.index.is_unique or not prices.index.is_monotonic_increasing:raise ValueError('Invalid price sessions')
    calendar=pd.DatetimeIndex(calendar);startloc=calendar.get_loc(pd.Timestamp(start));last=calendar.get_loc(pd.Timestamp(end))
    if startloc<warmup+1:raise ValueError('Insufficient source-calendar warmup')
    required=calendar[startloc-warmup-1:last+1];window=prices.reindex(required)
    rows=[]
    for symbol in sorted(prices.columns):
        p=window[symbol];reasons=[];finite=np.isfinite(p.to_numpy(float))&(p.to_numpy(float)>0)
        if not finite.all():reasons.append('missing_or_nonpositive_price')
        if official_tickers is not None and symbol not in official_tickers:reasons.append('not_in_supplied_official_US_registrant_ticker_snapshot')
        # Dot suffixed exchange codes are not US exchange symbols.
        if '.' in symbol:reasons.append('non_US_exchange_suffix')
        if volumes is not None:
            v=volumes.reindex(index=required,columns=prices.columns)[symbol]
            if not np.isfinite(v.to_numpy(float)).all() or (v<0).any():reasons.append('missing_or_invalid_volume')
        rows.append({'symbol':symbol,'price_complete':bool(finite.all()),'available_sessions':int(finite.sum()),'required_sessions':len(required),'first_available':str(prices[symbol].first_valid_index()),'last_available':str(prices[symbol].last_valid_index()),'raw_SEC_present':symbol in sec_symbols,'candidate_price_eligible':not reasons,'reasons':reasons})
    count=sum(r['candidate_price_eligible'] for r in rows)
    return {'target_assets':target_assets,'cached_symbols':len(prices.columns),'complete_prices_count':sum(r['price_complete'] for r in rows),'candidate_price_count':count,'raw_SEC_present_count':len(set(prices.columns)&set(sec_symbols)),'target_met_on_prices_only':count>=target_assets,'full_eligibility_verified':False,'full_eligibility_blockers':['Official ticker snapshot is current, not historical membership','Operating-company security type and listing identity require verification','Liquidity raw-price/share-volume alignment not independently verified','PIT SEC coverage and corporate actions need their own admission','Current universe excludes unavailable historical/delisted names'],'evaluation_first_return':start,'evaluation_last_return':end,'required_start':str(required[0]),'evaluation_sessions':last-startloc+1,'rows':rows,'selection_uses_performance':False}

def audit_snapshot(root):
    root=Path(root);raw=root/'inputs/raw_snapshot';database=raw/'research_data.sqlite'
    con=sqlite3.connect(f'file:{database}?mode=ro',uri=True)
    bars=pd.read_sql_query("SELECT ticker,substr(timestamp,1,10) date,close,volume FROM ohlcv_bars WHERE timeframe='1d' AND adjusted=1 AND timestamp>='2010-01-01'",con);con.close()
    if bars.duplicated(['ticker','date']).any():raise ValueError('Duplicate raw bars')
    prices=bars.pivot(index='date',columns='ticker',values='close');prices.index=pd.to_datetime(prices.index)
    volume=bars.pivot(index='date',columns='ticker',values='volume');volume.index=pd.to_datetime(volume.index)
    ticker_map=json.loads((raw/'sec/company_tickers.json').read_text());official={r['ticker']:r for r in ticker_map.values()}
    zpath=root/'inputs/archive/oryntra_sanitized_research_5.4.13.zip'
    with zipfile.ZipFile(zpath) as z:
        ciks={int(Path(n).stem[3:]) for n in z.namelist() if n.startswith('sec/companyfacts/') and n.endswith('.json') and Path(n).stem[3:].isdigit()}
        sec={s for s,r in official.items() if int(r['cik_str']) in ciks}
        sectors=[]
        for symbol in pd.read_csv(root/'inputs/final_prices.csv.gz',nrows=0).columns[1:]:
            row=official.get(symbol);path='sec/submissions/CIK'+str(row['cik_str']).zfill(10)+'.json' if row else None
            if path in z.namelist():
                submission=json.loads(z.read(path));sic=str(submission.get('sic') or '')
                sector='SIC_'+sic[:2] if sic else 'UNKNOWN'
            else:sector='UNKNOWN'
            sectors.append({'symbol':symbol,'sector':sector,'classification_timing':'current_snapshot_not_PIT'})
    calendar=prices['SPY'].dropna().index
    result=coverage(prices,calendar,start='2016-09-09',end='2026-09-18',warmup=252,target_assets=300,official_tickers=official,sec_symbols=sec,volumes=volume)
    old=json.loads((root/'inputs/old_universe_audit.json').read_text())
    result.update(raw_SEC_issuers=len(ciks),previous_strict_SEC_assets=183,official_ticker_snapshot='2026-09-20',source_database_sha256=digest(database),source_archive_sha256=digest(zpath),source_library_file_id='libfile_9726d3cd4f1c8191841d89711ea8b7cc',coverage_study_only=True)
    atomic_json(root/'reports/UNIVERSE_FEASIBILITY.json',result)
    pd.DataFrame(result['rows']).to_csv(root/'reports/universe_symbol_audit.csv',index=False)
    pd.DataFrame(sectors).to_csv(root/'inputs/current_SIC_labels.csv',index=False)
    lines=['# Expanded-universe feasibility','',f"Target: 300 first, then 500. Saved source: {result['cached_symbols']} symbols.",f"Complete positive prices on the exact evaluation and 252-session warmup grid: {result['complete_prices_count']}.",f"After available official-US ticker snapshot and volume checks: {result['candidate_price_count']} price candidates; this is not a fully qualified universe.",f"Raw SEC issuers: {len(ciks)}; previously proven strict complete SEC panel: 183.",'','The 300–500 ten-year target is BLOCKED on data coverage. No smaller panel is relabeled as the target.','',*['- '+s for s in result['full_eligibility_blockers']],'','Admission policy before any expanded test: use an official listing/security master with historical identities; at each cutoff require only then-known listing, history and trailing liquidity. Preserve unavailable and delisted names in the candidate log. Require verified price/share/volume basis and any enabled SEC/macro lineage. Use deterministic symbol-order tie breaks only after performance-independent gates. An all-period complete static subset remains a survivorship/coverage-conditioned diagnostic.']
    (root/'reports/UNIVERSE_FEASIBILITY.md').write_text('\n'.join(lines)+'\n');return result
