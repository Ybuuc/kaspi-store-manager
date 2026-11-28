"""Measure the local baseline; optionally measure Ollama explicitly without private data."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kaspibot.services.assistant_reference import local_reference


def evaluate(dataset, backend='baseline', model=None):
    import requests
    cases = json.loads(Path(dataset).read_text(encoding='utf-8'))
    results = []
    for case in cases:
        if backend == 'baseline':
            response = local_reference(case['question'])
            answer = response['reply'] if response else ''
            actual = response['intent'] if response else 'unknown'
        else:
            if not model:
                raise ValueError('Set --model to an already installed Ollama model')
            response = requests.post('http://127.0.0.1:11434/api/generate', timeout=120,
                                     json=dict(model=model, stream=False, options={'temperature': 0},
                                               prompt='Ответь кратко по справке. Справка: ' +
                                               json.dumps(local_reference(case['question']), ensure_ascii=False) +
                                               '\nВопрос: ' + case['question']))
            response.raise_for_status()
            answer = response.json()['response']
            actual = None
        passed = actual == case['intent'] if backend == 'baseline' else all(term.casefold() in answer.casefold() for term in case['required_terms'])
        results.append(dict(id=case['id'], question=case['question'], expected=case['reference'],
                            answer=answer, passed=passed))
    return dict(backend=backend, model=model, count=len(results),
                passed=sum(r['passed'] for r in results),
                score=sum(r['passed'] for r in results)/len(results), results=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('docs/course/ai/eval.json'))
    parser.add_argument('--backend', choices=['baseline', 'ollama'], default='baseline')
    parser.add_argument('--model')
    parser.add_argument('--output', type=Path, default=Path('docs/course/ai/baseline-results.json'))
    args = parser.parse_args()
    result = evaluate(args.dataset, args.backend, args.model)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))
