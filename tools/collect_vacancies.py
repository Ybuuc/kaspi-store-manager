"""Collect ten current Kazakhstan PM/Delivery/PO/BA-PM vacancies without copying ads."""
import json
import re
from datetime import date
from pathlib import Path

import requests


SKILLS = {
    'Agile/Scrum/Kanban': ['agile', 'scrum', 'kanban'],
    'Jira/Confluence': ['jira', 'confluence'],
    'Requirements/BA': ['требован', 'requirements', 'бизнес-анализ', 'business analysis'],
    'Stakeholders/communication': ['stakeholder', 'коммуникац', 'заказчик', 'стейкхолдер'],
    'Planning/delivery': ['планирован', 'срок', 'delivery', 'roadmap'],
    'Risk/budget': ['риск', 'бюджет', 'budget', 'risk'],
    'Analytics/SQL': ['sql', 'аналитик', 'analytics'],
    'English': ['английск', 'english'],
    'Experience': ['опыт', 'experience'],
    'Product discovery/metrics': ['discovery', 'гипотез', 'product metric', 'продуктов'],
    'API/integrations': [' api', 'интеграц', 'rest'],
    'Process modelling': ['bpmn', 'uml', 'моделирован'],
    'Excel/Sheets': ['excel', 'google sheets'],
    'Team leadership': ['команд', 'team', 'лидер'],
    'Certifications': ['pmp', 'psm', 'сертифик'],
}


def collect():
    session = requests.Session()
    session.headers['User-Agent'] = 'CourseResearch/1.0 (public-vacancy-analysis)'
    candidates = {}
    for query in ['project manager', 'delivery manager', 'product owner', 'business analyst project manager']:
        response = session.get('https://api.hh.ru/vacancies',
                               params={'area':40, 'text':query, 'per_page':50}, timeout=30)
        response.raise_for_status()
        for row in response.json().get('items', []):
            name = row['name'].casefold()
            if re.search(r'project|delivery|product owner|проект|проджект|ba.?pm', name):
                candidates[row['id']] = row
    rows = []
    for vacancy in candidates.values():
        response = session.get(vacancy['url'], timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get('archived'):
            continue
        description = re.sub('<[^>]+>', ' ', data['description']).casefold()
        rows.append(dict(id=data['id'], title=data['name'], url=data['alternate_url'],
                         employer=data['employer']['name'], published_at=data['published_at'],
                         skills=[s for s, terms in SKILLS.items() if any(t in description for t in terms)],
                         experience=data.get('experience', {}).get('name'),
                         tools=[k['name'] for k in data.get('key_skills', [])]))
        if len(rows) == 10:
            break
    if len(rows) < 10:
        raise RuntimeError(f'Only {len(rows)} verified active matching vacancies; do not invent the remainder')
    frequencies = {skill:sum(skill in r['skills'] for r in rows) for skill in SKILLS}
    result = dict(collected_on=str(date.today()), source='HeadHunter public API; area=40 Kazakhstan',
                  method='Keyword coding of full descriptions; manual completeness review required',
                  vacancies=rows, frequencies={s:dict(count=n, percent=n*10,
                  group='>=50%' if n>=5 else '20-<50%' if n>=2 else '<20%') for s,n in frequencies.items()})
    target=Path('docs/course/lz1/vacancies.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(verified_active=len(rows), output=str(target))))


if __name__=='__main__':
    collect()
