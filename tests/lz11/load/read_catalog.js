// k6 read-only benchmark. Use an isolated fixture database and a dedicated employee.
// To try NFR-01, override KSM_VUS=200 on an appropriately provisioned test server.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = (__ENV.KSM_BASE_URL || 'http://127.0.0.1:5000').replace(/\/$/, '');
const USER = __ENV.KSM_USER;
const PASSWORD = __ENV.KSM_PASSWORD;
if (!USER || !PASSWORD) {
  throw new Error('Set KSM_USER and KSM_PASSWORD for the dedicated synthetic employee');
}
let authenticated = null;  // k6 isolates global state per VU

export const options = {
  // Persist each VU session cookie across iterations; k6 resets it by default.
  noCookiesReset: true,
  vus: Number(__ENV.KSM_VUS || 5),
  duration: __ENV.KSM_DURATION || '30s',
  thresholds: {
    'http_req_duration{endpoint:products}': ['p(95)<800'],
    'http_req_failed{endpoint:products}': ['rate<0.01'],
    'checks': ['rate>0.99'],
  },
};

export default function () {
  if (authenticated === null) {
    const response = http.post(`${BASE}/login`,
      { username: USER, password: PASSWORD },
      { tags: { endpoint: 'login' } });
    authenticated = check(response, {
      'employee login succeeds': r => r.status === 200 && r.body.includes('Тауарлар'),
    });
  }
  if (!authenticated) {
    sleep(1);
    return;
  }
  const response = http.get(`${BASE}/products`, { tags: { endpoint: 'products' } });
  check(response, {
    'catalog is authenticated and readable': r =>
      r.status === 200 && r.body.includes('Тауарлар') && !r.body.includes('Жүйеге кіру</h1>'),
  });
  sleep(1);
}
