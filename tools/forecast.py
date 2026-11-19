"""10,000-run bootstrap forecast from real issue closure dates, including zero weeks."""
import argparse
import json
import math
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


def throughput(data):
    issues = data['issues']
    first = min(date.fromisoformat(i['created_at'][:10]) for i in issues)
    first -= timedelta(days=first.weekday())
    end = date.fromisoformat(data['collected_date'])
    end -= timedelta(days=end.weekday())
    counts = Counter()
    for issue in issues:
        if issue['closed_at']:
            day = date.fromisoformat(issue['closed_at'][:10])
            if first <= day < end:
                counts[(day - first).days // 7] += 1
    return [counts[i] for i in range((end - first).days // 7)]


def simulate(history, remaining, runs=10000, seed=20261119):
    if not history or any(x < 0 for x in history) or not any(history) or remaining < 1:
        raise ValueError('Need nonnegative history with completions and positive remaining scope')
    rng = random.Random(seed)
    durations = []
    for _ in range(runs):
        done, weeks = 0, 0
        while done < remaining:
            done += rng.choice(history)
            weeks += 1
        durations.append(weeks)
    ordered = sorted(durations)
    return dict(runs=runs, seed=seed, remaining=remaining, weekly_throughput=history,
                quantiles_weeks={f'P{p}': ordered[math.ceil(runs*p/100)-1] for p in (50, 70, 85, 95)},
                weeks=durations)


def report(source, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    data = json.loads(Path(source).read_text(encoding='utf-8'))
    remaining = sum(i['state'] == 'open' for i in data['issues'])
    result = simulate(throughput(data), remaining)
    start = date.fromisoformat(data['collected_date']) + timedelta(days=1)
    result['start'] = str(start)
    result['dates'] = {p: str(start + timedelta(weeks=n)) for p, n in result['quantiles_weeks'].items()}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    durations = result.pop('weeks')
    (output / 'forecast.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].hist(durations, bins=range(1, max(durations)+2), color='#16848f', edgecolor='white')
    axes[0].set(xlabel='Weeks from forecast start', ylabel='Simulations', title='Bootstrap: 10,000 runs')
    ordered = sorted(durations)
    axes[1].plot(ordered, [(i+1)/len(ordered) for i in range(len(ordered))], color='#16848f')
    axes[1].set(xlabel='Weeks from forecast start', ylabel='Cumulative probability', title='Completion probability', ylim=(0,1))
    fig.tight_layout()
    fig.savefig(output / 'forecast.png', dpi=160)
    plt.close(fig)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('docs/course/data/issues.json'))
    parser.add_argument('--output', type=Path, default=Path('docs/course/lz6'))
    args = parser.parse_args()
    report(args.source, args.output)
