# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import time
import random

BASE_URL = 'http://localhost:8000'

class FullStressTest:
    def __init__(self):
        self.tokens = []

    async def login(self, session, username, password):
        try:
            data = aiohttp.FormData()
            data.add_field('username', username)
            data.add_field('password', password)
            async with session.post(f'{BASE_URL}/api/v1/auth/login',
                data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get('data', {}).get('access_token')
        except Exception as e:
            print(f"Login error: {e}")
        return None

    async def run_test(self):
        print('='*70)
        print('  DocMind RAG Full Stress Test v6 - Extreme Validation')
        print('='*70)

        async with aiohttp.ClientSession() as session:
            # 1. Get tokens
            print('\n[1] Getting auth tokens (20 tokens)...')
            for i in range(20):
                token = await self.login(session, 'guest', '123456')
                if token:
                    self.tokens.append(token)
            print(f'    Got {len(self.tokens)} tokens')

            # 2. Health endpoint burst
            print('\n[2] GET /health burst (1000 requests)...')
            start = time.time()

            async def health_req(i):
                try:
                    async with session.get(f'{BASE_URL}/health') as resp:
                        return resp.status == 200
                except:
                    return False

            tasks = [health_req(i) for i in range(1000)]
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start
            success = sum(results)
            print(f'    QPS: {1000/elapsed:.1f} | Success: {success}/1000')

            if self.tokens:
                # 3. Auth endpoint extreme
                print('\n[3] GET /auth/me extreme burst (2000 concurrent)...')
                start = time.time()

                async def auth_me(i):
                    h = {'Authorization': f'Bearer {self.tokens[i % len(self.tokens)]}'}
                    try:
                        async with session.get(f'{BASE_URL}/api/v1/auth/me', headers=h) as resp:
                            return resp.status == 200
                    except:
                        return False

                tasks = [auth_me(i) for i in range(2000)]
                results = await asyncio.gather(*tasks)
                elapsed = time.time() - start
                success = sum(results)
                print(f'    QPS: {2000/elapsed:.1f} | Success: {success}/2000')

                # 4. Mixed load test
                print('\n[4] Mixed API burst (1000 concurrent)...')
                start = time.time()

                async def mixed_req(i):
                    endpoint = random.choice(['/health', '/api/v1/auth/me', '/api/v1/files/list', '/api/v1/knowledge/'])
                    h = {'Authorization': f'Bearer {self.tokens[i % len(self.tokens)]}'}
                    try:
                        async with session.get(f'{BASE_URL}{endpoint}', headers=h) as resp:
                            return resp.status in [200, 401, 403]
                    except:
                        return False

                tasks = [mixed_req(i) for i in range(1000)]
                results = await asyncio.gather(*tasks)
                elapsed = time.time() - start
                success = sum(results)
                print(f'    QPS: {1000/elapsed:.1f} | Success: {success}/1000')

                # 5. 60-second sustained test
                print('\n[5] Sustained load test (60 seconds)...')
                total_requests = 0
                total_success = 0
                start_time = time.time()
                rounds = 0

                while time.time() - start_time < 60:
                    rounds += 1
                    tasks = [auth_me(i) for i in range(100)]
                    results = await asyncio.gather(*tasks)
                    total_requests += 100
                    total_success += sum(results)
                    await asyncio.sleep(0.05)

                elapsed = time.time() - start_time
                print(f'    Total: {total_requests} requests | Success: {total_success} ({total_success/total_requests*100:.1f}%) | Avg QPS: {total_requests/elapsed:.1f}')

                # 6. Extreme progressive test
                print('\n[6] Extreme progressive load test...')
                for n in [500, 1000, 2000, 3000, 5000]:
                    start = time.time()
                    tasks = [health_req(i) for i in range(n)]
                    results = await asyncio.gather(*tasks)
                    elapsed = time.time() - start
                    success = sum(results)
                    print(f'    {n} concurrent: QPS={n/elapsed:.1f} | Success={success}/{n}')

                # 7. Auth progressive test
                print('\n[7] Auth progressive load test...')
                for n in [500, 1000, 2000, 3000]:
                    start = time.time()
                    tasks = [auth_me(i) for i in range(n)]
                    results = await asyncio.gather(*tasks)
                    elapsed = time.time() - start
                    success = sum(results)
                    print(f'    {n} concurrent: QPS={n/elapsed:.1f} | Success={success}/{n}')

            print('\n' + '='*70)
            print('  ALL EXTREME TESTS COMPLETE')
            print('='*70)

if __name__ == '__main__':
    asyncio.run(FullStressTest().run_test())
