"""Run only against a disposable demo: locust -f tests/locustfile.py --headless -u 200 -r 20 -t 60s."""
import os

from locust import HttpUser, task, between


class CatalogueUser(HttpUser):
    wait_time=between(0.5,2)

    def on_start(self):
        self.client.post('/management/login',data=dict(username=os.environ['LOAD_USERNAME'],
                                                     password=os.environ['LOAD_PASSWORD']))

    @task(3)
    def catalogue(self):
        self.client.get('/products',name='archive_catalogue')

    @task
    def search(self):
        self.client.get('/products?q=test',name='archive_search')
