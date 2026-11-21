"""Transparent 36-month TCO, 12-month budget and scenario analysis; all costs are estimates."""
import csv
import json
import math
from pathlib import Path


def calculate(inputs):
    development = inputs['development_hours'] * inputs['hourly_cost_kzt']
    training = inputs['training_hours'] * inputs['hourly_cost_kzt']
    benefit = inputs['saved_hours_month'] * inputs['hourly_cost_kzt']
    result = {}
    for name, variant in inputs['variants'].items():
        initial = development + training + variant['hardware_kzt']
        monthly = variant['infrastructure_kzt_month'] + variant['licenses_kzt_month'] + inputs['support_hours_month'] * inputs['hourly_cost_kzt']
        tco = initial + 36 * monthly
        annual = initial + 12 * monthly
        margin_per_user = inputs['price_user_month'] - inputs['variable_cost_user_month']
        result[name] = dict(initial_kzt=initial, monthly_kzt=monthly, tco_36_kzt=tco,
                            budget_12_kzt=annual, roi=(36*benefit-tco)/tco,
                            roi_cost_plus30=(36*benefit-1.3*tco)/(1.3*tco),
                            roi_effect_half=(18*benefit-tco)/tco,
                            payback_months=initial/(benefit-monthly) if benefit>monthly else None,
                            cost_user_month=annual/(12*inputs['users']),
                            break_even_users=math.ceil((initial/12+monthly)/margin_per_user)
                            if margin_per_user>0 else None)
    return result


def report():
    root = Path(__file__).resolve().parents[1] / 'docs/course/finance'
    data = json.loads((root / 'inputs.json').read_text(encoding='utf-8'))
    results = calculate(data)
    (root / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    with (root / 'budget-12-months.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['variant', 'month', 'development_and_training_and_hardware_kzt', 'infra_kzt', 'licenses_kzt', 'support_kzt', 'total_kzt'])
        for name, variant in data['variants'].items():
            for month in range(1,13):
                initial = results[name]['initial_kzt'] if month==1 else 0
                writer.writerow([name, month, initial, variant['infrastructure_kzt_month'], variant['licenses_kzt_month'],
                                 data['support_hours_month']*data['hourly_cost_kzt'], initial+results[name]['monthly_kzt']])
    print(json.dumps(results, indent=2))
    return results


if __name__ == '__main__':
    report()
