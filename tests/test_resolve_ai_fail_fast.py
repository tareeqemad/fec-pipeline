"""AI resolution must stop before batching when the provider has no credits."""
import pandas as pd
import pytest

from fec.resolve.pipeline.ai_client import AIQuotaExhausted, is_ai_quota_error
from fec.resolve.pipeline.steps import ai_employer
from fec.resolve.pipeline import cli as resolve_cli


class _ProviderError(Exception):
    def __init__(self, body):
        super().__init__(body.get('message', 'provider error'))
        self.body = body


class _Cache:
    def __init__(self):
        self.saves = 0

    def save(self):
        self.saves += 1


def test_detects_credit_error_but_not_temporary_rate_limit():
    exhausted = _ProviderError({
        'message': 'You have no credits remaining.',
        'type': 'insufficient_quota',
        'code': 'credit_balance_exhausted',
    })
    temporary = _ProviderError({
        'message': 'Rate limit reached; retry later.',
        'type': 'rate_limit_error',
        'code': 'rate_limit_exceeded',
    })

    assert is_ai_quota_error(exhausted)
    assert not is_ai_quota_error(temporary)


def test_credit_error_stops_on_preflight_without_storing_not_found(monkeypatch):
    calls = []
    stored = []
    cache = _Cache()

    def no_credits(_client, _model, _system_prompt, user_prompt):
        calls.append(user_prompt)
        raise _ProviderError({
            'message': 'You have no credits remaining.',
            'type': 'insufficient_quota',
            'code': 'credit_balance_exhausted',
        })

    monkeypatch.setattr(ai_employer, 'ai_web_search_call', no_credits)

    with pytest.raises(AIQuotaExhausted):
        ai_employer.run_web_search(
            client=None, model='model', system_prompt='system',
            items=['EMPLOYER 1', 'EMPLOYER 2', 'EMPLOYER 3'],
            build_prompt=lambda item: item,
            store_fn=lambda item, obj: stored.append((item, obj)),
            cache=cache, label='AI Lookup',
        )

    assert calls == ['EMPLOYER 1']
    assert stored == []
    assert cache.saves == 0


def test_cli_writes_cached_results_before_reporting_partial(tmp_path, monkeypatch):
    csv_path = tmp_path / 'contributions_cleaned.csv'
    csv_path.write_text('placeholder\n', encoding='utf-8')
    source = pd.DataFrame({
        'contribution_receipt_amount': [100],
        'contributor_employer': ['CACHED COMPANY'],
    })
    applied = []

    monkeypatch.setattr('fec.io.read_pipeline_csv', lambda _path: source.copy())
    monkeypatch.setattr(resolve_cli, 'load_env', lambda: None)
    monkeypatch.setattr(resolve_cli, '_compute_donor_totals', lambda _df: pd.DataFrame())
    monkeypatch.setattr(resolve_cli, 'load_manual_locations', lambda *args: None)
    monkeypatch.setattr(resolve_cli, 'load_manual_previous_employers', lambda *args: None)
    monkeypatch.setattr(resolve_cli, 'load_manual_committee_overrides', lambda *args: None)
    monkeypatch.setattr(resolve_cli, 'step_cross_record', lambda *args: 0)
    monkeypatch.setattr(resolve_cli, 'step_fec_api', lambda *args, **kwargs: 0)
    monkeypatch.setattr(resolve_cli, 'get_ai_provider_model', lambda: ('openai', 'model'))
    monkeypatch.setattr(
        resolve_cli, 'step_ai_lookup',
        lambda *args, **kwargs: (_ for _ in ()).throw(AIQuotaExhausted('no credits')),
    )
    monkeypatch.setattr(resolve_cli, 'dedup_by_resolved_address',
                        lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(resolve_cli, 'step_committees_own_address', lambda *args: 0)
    monkeypatch.setattr(resolve_cli, 'show_stats', lambda *args: None)

    def apply_cached(df, *_caches):
        applied.append(True)
        return df.assign(employer_city='NEW YORK')

    monkeypatch.setattr(resolve_cli, 'apply_results', apply_cached)
    monkeypatch.setattr(
        'sys.argv', ['resolve.py', str(csv_path), '--apply'],
    )

    with pytest.raises(SystemExit) as stopped:
        resolve_cli.main()

    assert stopped.value.code == 2
    assert applied == [True]
    written = pd.read_csv(csv_path)
    assert written['employer_city'].tolist() == ['NEW YORK']
    assert not csv_path.with_suffix('.csv.tmp').exists()
