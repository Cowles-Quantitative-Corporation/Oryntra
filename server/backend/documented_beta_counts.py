from __future__ import annotations

from datetime import datetime
from .database import get_app_counter, set_app_counter_min

DOCUMENTED_BETA_STOCK_ANALYSES = 2487
DOCUMENTED_BETA_ENGINE_CHECKS = 9499


def sync_documented_beta_counts() -> dict:
    before_stock = get_app_counter('stock_searches')
    before_lab = get_app_counter('pattern_lab_stock_analyses')
    before_checks = get_app_counter('pattern_lab_engine_checks')
    after_stock = set_app_counter_min('stock_searches', DOCUMENTED_BETA_STOCK_ANALYSES)
    after_lab = set_app_counter_min('pattern_lab_stock_analyses', DOCUMENTED_BETA_STOCK_ANALYSES)
    after_checks = set_app_counter_min('pattern_lab_engine_checks', DOCUMENTED_BETA_ENGINE_CHECKS)
    set_app_counter_min('documented_beta_counter_floor_v020', 1)
    return {
        'synced_at': datetime.utcnow().isoformat(),
        'documented_beta_stock_analyses': DOCUMENTED_BETA_STOCK_ANALYSES,
        'documented_beta_engine_checks': DOCUMENTED_BETA_ENGINE_CHECKS,
        'before': {'stock_searches': before_stock, 'pattern_lab_stock_analyses': before_lab, 'pattern_lab_engine_checks': before_checks},
        'after': {'stock_searches': after_stock, 'pattern_lab_stock_analyses': after_lab, 'pattern_lab_engine_checks': after_checks},
        'added_visible_floor': max(0, after_stock - before_stock),
        'added_engine_floor': max(0, after_checks - before_checks),
        'note': 'Counters were raised only to the documented beta-test floor; existing higher counters were preserved.'
    }

