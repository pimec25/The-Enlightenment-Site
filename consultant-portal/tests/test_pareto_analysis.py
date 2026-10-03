from datetime import date, datetime

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook

from analyze_sales_periods import analyze
from pareto_analysis import detailed_analysis, ranking


def row(customer, item, sales, cost):
    return dict(customer=customer, item=item, sales=sales, cost=cost)


def test_crossing_group_top_twenty_and_ties():
    groups = [dict(id=k, sales=v) for k, v in [('b', 40), ('a', 40), ('c', 20), ('d', -10)]]
    result = ranking(groups, 'sales')
    assert [r['id'] for r in result['rows']] == ['a', 'b', 'c', 'd']
    assert result['groups_to_80'] == 2
    assert result['coverage_pct'] == 80
    assert result['top_20_count'] == 1
    assert result['top_20_share_pct'] == 40
    assert result['net_total'] == 90
    assert result['negative_total'] == -10
    assert result['rows'][-1]['share_pct'] == 0


def test_quads_loss_credit_and_reconciliation():
    rows = [row('a', 'p1', 60, 30), row('b', 'p2', 25, 24),
            row('c', 'p1', 10, 2), row('d', 'p3', 5, 7), row('e', 'p4', -2, 0),
            row('f', 'p4', 0, 3)]
    result = detailed_analysis(rows, [row('old', 'old-product', 100, 60)])
    customers = result['dimensions']['customer']
    assigned = {r['id']: r['quad'] for r in customers['sales']['rows']}
    assert assigned == dict(a='Q1', b='Q2', c='Q3', d='Q4', e='Review', f='Review')
    assert customers['gross_loss']['positive_base'] == 7
    assert customers['prior_only'][0]['id'] == 'old'
    for cells in [customers['quads'], result['dimensions']['item']['quads'], result['customer_product_matrix']]:
        assert sum(c['sales'] for c in cells) == 98
        assert sum(c['gross_profit'] for c in cells) == 32
        assert sum(c['gross_loss'] for c in cells) == 7
        assert sum(c['row_count'] for c in cells) == 6


@pytest.mark.parametrize('rows', [[], [row('a', 'p', 0, 10)], [row('a', 'p', -5, 0)]])
def test_no_positive_base(rows):
    result = detailed_analysis(rows, [])
    assert result['margin_threshold_pct'] is None
    for dimension in result['dimensions'].values():
        assert dimension['sales']['groups_to_80'] == 0
        assert dimension['sales']['coverage_pct'] is None
        assert all(r['quad'] == 'Review' for r in dimension['sales']['rows'])


def test_equality_is_high_margin():
    result = detailed_analysis([row('a', 'p', 80, 40), row('b', 'p', 20, 10)], [])
    assert [r['quad'] for r in result['dimensions']['customer']['sales']['rows']] == ['Q1', 'Q3']


def test_single_year_workbook_and_saved_template(tmp_path):
    book = Workbook()
    book.active.append(['Request Date', 'Extended Price', 'Extended Cost', 'Parent Number',
                        '2nd Item Number', 'Value Stream Product Family', 'Order Number', 'Line Number', 'Customer Name'])
    book.active.append([datetime(2026, 1, 1), 100, 70, 'c1', 'p1', 'stream', 'order', 1, '<script>alert(1)</script>'])
    book.active.append([datetime(2026, 12, 1), 1000, 700, 'c2', 'p2', 'stream', 'order', 2, 'Future'])
    path = tmp_path / 'sales.xlsx'
    book.save(path)
    result = analyze(path, date(2026, 9, 30))
    assert result['change']['gross_margin_points'] is None
    assert result['future_dated_rows_excluded'] == 1
    assert result['detailed_80_20']['dimensions']['customer']['sales']['total_groups'] == 1
    env = Environment(loader=FileSystemLoader('templates'), autoescape=select_autoescape())
    rendered = env.get_template('pareto.html').render(report=result)
    assert 'Detailed 80/20 analysis' in rendered
    assert '<script>alert' not in rendered
    assert '&lt;script&gt;' in rendered
    assert 'Future' not in rendered
    assert env.get_template('pareto.html').render(report={}).strip() == ''
    record_html = env.get_template('record.html').render(record=dict(filename='test', created_at='now', kind='upload', report=result), key='test')
    assert 'Customer profitability QUADs' in record_html
