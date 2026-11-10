"""Regression tests: python3 -m unittest -v."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
from types import SimpleNamespace
import parser

spec = importlib.util.spec_from_file_location('scope', Path(__file__).with_name('llm-scope.py'))
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)
SUMMARY = 'strata serve: prompt 1,000 tokens = 900 reused + 100 read in 200 ms (500.0 tok/s), 50 generated in 1000 ms (50.0 tok/s), drafts accepted 20 of 25, 3 checkpoints'


class ParserTests(unittest.TestCase):
    def test_summary_is_complete_and_honest(self):
        events = {e['type']: e for e in parser.parse_line(SUMMARY)}
        self.assertEqual(events['done']['tok_s'], 50)
        self.assertIsNone(events['done']['finish'])
        self.assertIsNone(events['done']['cancel'])
        self.assertIsNone(events['prefill_sum']['chunks'])
        self.assertEqual(events['req']['prompt_tokens'], 1000)
        self.assertIsNone(events['req']['stream'])
        self.assertEqual(events['spec']['source'], 'mtp')
        self.assertEqual(events['reuse']['reused'], 900)

    def test_optional_metrics_independent(self):
        expert = parser.parse_line('strata serve: decode expert cache hit rate: 77.2% (148282 hits / 192053 lookups); other data')
        self.assertEqual(expert['hit_pct'], 77.2)
        self.assertEqual(parser.parse_line('strata serve: suffix drafts: 2 windows, 0 of 0 drafts accepted')['source'], 'suffix')
        self.assertEqual(parser.parse_line('strata serve: KV streaming: 99.47% of 1,000 block reads hit VRAM, 327.0 MiB read from RAM')['blocks'], 1000)

    def test_cache_and_lifecycle(self):
        parked = parser.parse_line('strata serve: conversation cache: parked 53 tokens in 91.4 ms; parked=1 bytes=240000000')
        self.assertEqual(parked['cache_gb'], .24)
        restored = parser.parse_line('strata serve: conversation cache: restored 53 tokens (live) in 79.7 ms')
        self.assertEqual(restored['type'], 'cache_restore')
        self.assertEqual(parser.parse_line('strata-ud-iq2xs: ready instance abc http://localhost:8080')['type'], 'engine_ready')
        self.assertEqual(parser.parse_line('strata-ud-iq2xs: stopped')['type'], 'engine_exit')

    def test_zero_read(self):
        events = parser.parse_line(SUMMARY.replace('900 reused + 100 read', '1000 reused + 0 read'))
        prefill = next(e for e in events if e['type'] == 'prefill_sum')
        self.assertIsNone(prefill['tok_s'])

    def test_legacy_and_unknown(self):
        self.assertEqual(parser.parse_line('glm spec: 215 of 239 drafts accepted (90.0%)')['pct'], 90)
        self.assertEqual(parser.parse_line('unrecognized')['type'], 'log')
        self.assertIsNone(parser.parse_line('   '))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patches = [patch.object(scope, 'DATA', Path(self.temp.name) / 'events.jsonl'), patch.object(scope, 'EVENTS', deque(maxlen=200000)), patch.object(scope, '_seq', 0)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_pagination_equal_timestamps(self):
        with patch.object(scope.time, 'time', return_value=123):
            for i in range(5100):
                scope.record({'type': 'test', 'n': i})
        cursor, seen = 0, []
        while True:
            page = scope.event_page(cursor)
            seen.extend(e['n'] for e in page['events'])
            cursor = page['cursor']
            if not page['more']:
                break
        self.assertEqual(seen, list(range(5100)))
        scope.record({'type': 'late'})
        self.assertEqual(scope.event_page(cursor)['events'][0]['type'], 'late')

    def test_history_corruption_and_retention(self):
        scope.DATA.write_text('{bad\n'+json.dumps({'type':'test','ts':1,'id':10})+'\n{"partial":')
        scope.load_history()
        self.assertEqual(scope._seq, 10)
        with patch.object(scope, 'MAX_FILE_BYTES', 1):
            scope.record({'type':'next'})
        self.assertTrue(scope.DATA.with_suffix('.jsonl.1').exists())
        self.assertEqual(scope.EVENTS[-1]['id'], 11)

    def test_append_after_incomplete_record(self):
        scope.DATA.write_text('{"partial":')
        scope.record({'type': 'valid'})
        scope.EVENTS.clear()
        scope._seq = 0
        scope.load_history()
        self.assertEqual([e['type'] for e in scope.EVENTS], ['valid'])

    def test_gpu_missing_power(self):
        stop = threading.Event()
        original = scope.record
        def consume(ev):
            original(ev)
            stop.set()
        with patch.object(scope.shutil, 'which', return_value='/usr/bin/nvidia-smi'), patch.object(scope.subprocess, 'run', return_value=SimpleNamespace(stdout='99, 70, [N/A], 12000, 4000\n')), patch.object(scope, 'record', side_effect=consume):
            scope.gpu_sampler(stop, gpu=2)
        self.assertEqual(scope.EVENTS[-1]['u'], 99)
        self.assertIsNone(scope.EVENTS[-1]['p'])
        self.assertEqual(scope.EVENTS[-1]['gpu'], 2)

    def test_http_api_validation_and_headers(self):
        server = scope.ThreadingHTTPServer(('localhost', 0), scope.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f'http://localhost:{server.server_port}'
            scope.record({'type': 'test'})
            with urlopen(base+'/api/events?after=0') as response:
                self.assertEqual(json.load(response)['events'][0]['id'], 1)
                self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
                self.assertIn("object-src 'none'", response.headers['Content-Security-Policy'])
            with self.assertRaises(HTTPError) as exc:
                urlopen(base+'/api/events?after=nan')
            self.assertEqual(exc.exception.code, 400)
            exc.exception.close()
            with self.assertRaises(HTTPError) as exc:
                urlopen(base+'/api/events-extra')
            self.assertEqual(exc.exception.code, 404)
            exc.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_concurrent_writes(self):
        threads = [threading.Thread(target=lambda: [scope.record({'type':'test'}) for _ in range(30)]) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        ids = [json.loads(line)['id'] for line in scope.DATA.read_text().splitlines()]
        self.assertEqual(ids, list(range(1,121)))

    def test_gap_and_reset(self):
        scope.EVENTS = deque(maxlen=2)
        for i in range(4): scope.record({'type':'test'})
        self.assertTrue(scope.event_page(1)['gap'])
        self.assertTrue(scope.event_page(100)['reset'])

    def test_disk_fields(self):
        text = '259 0 nvme0n1 1 2 300 4 5 6 700 8 9 10 11 0 0'
        self.assertEqual(scope.disk_counters(text, 'nvme0n1'), (300,700,9))
        self.assertIsNone(scope.disk_counters(text, 'sda'))

    def test_net_counters(self):
        text = ('Inter-|   Receive |  Transmit\n'
                ' face |bytes packets errs drop fifo frame compressed multicast|bytes packets\n'
                '    lo: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n'
                '  eth0: 3000000 1 2 3 4 5 6 7 4000000 8 9 10 11 12 13 14\n')
        self.assertEqual(scope.net_counters(text, 'eth0'), (3000000, 4000000))
        self.assertIsNone(scope.net_counters(text, 'wlan0'))

    def test_count_connections(self):
        text = ('State Recv-Q Send-Q Local Address:Port Peer Address:Port\n'
                'ESTAB 0 0 127.0.0.1:8080 127.0.0.1:55 users:(("x",pid=1,fd=2))\n'
                'ESTAB 0 0 [::1]:8080 [::1]:56\n'
                'ESTAB 0 0 127.0.0.1:9999 127.0.0.1:57\n'
                'ESTAB 0 0 broken\n'
                'SYN-SENT 0 1 127.0.0.1:58 127.0.0.1:8080\n')
        self.assertEqual(scope.count_connections(text, 8080), 2)

    def test_default_iface_skips_loopback_reject_route(self):
        texts = {'/proc/net/route': 'Iface\tDestination\tGateway\tFlags\nlo\t00000000\t00000000\t0005\neth0\t00000000\t01CDA8C0\t0003\n'}
        class FakePath:
            def __init__(self, p): self.p = str(p)
            def read_text(self): return texts[self.p]
        with patch.object(scope, 'Path', FakePath):
            self.assertEqual(scope.default_iface(), 'eth0')

    def test_serve_port_detection(self):
        d = Path(self.temp.name)
        (d / 'run-x.sh').write_text('exec server.py "--port" "8123"\n')
        self.assertEqual(scope.serve_port(d / 'strata-x.log'), 8123)
        self.assertEqual(scope.serve_port(d / 'strata-missing.log'), 8080)

    def test_sampler_net_and_conns(self):
        texts = {
            '/proc/pressure/io': 'some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=1.00 avg60=0.00 avg300=0.00 total=0\n',
            '/proc/diskstats': '259 0 nvme0n1 1 2 300 4 5 6 700 8 9 10 11 0 0\n',
            '/proc/meminfo': 'MemTotal: 1 kB\nDirty: 2 kB\nWriteback: 0 kB\n',
            '/proc/net/dev': ' eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n',
        }
        class FakePath:
            def __init__(self, p): self.p = str(p)
            def read_text(self): return texts[self.p]
        stop = threading.Event()
        original = scope.record
        def consume(ev):
            original(ev)
            stop.set()
        run = SimpleNamespace(stdout='ESTAB 0 0 127.0.0.1:8080 127.0.0.1:1\nESTAB 0 0 127.0.0.1:8080 127.0.0.1:2\n')
        with patch.object(scope, 'Path', FakePath), patch.object(scope.shutil, 'which', return_value='/usr/bin/ss'), patch.object(scope.subprocess, 'run', return_value=run), patch.object(scope, 'record', side_effect=consume):
            scope.sampler(stop, iface='eth0', port=8080)
        ev = scope.EVENTS[-1]
        self.assertEqual(ev['conns'], 2)
        self.assertEqual(ev['iface'], 'eth0')
        self.assertIsNone(ev['net_rx_kbs'])  # first sample has no rate yet

    def test_tail_partial_rotation_truncation_and_missing_file(self):
        path = Path(self.temp.name) / 'engine.log'
        path.write_text('old\n')
        stop, seen = threading.Event(), []
        thread = threading.Thread(target=scope.tail, args=(path, seen.append, .01, stop))
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(1)))
        time.sleep(.05)
        with path.open('a') as f: f.write('part')
        time.sleep(.04)
        self.assertEqual(seen, [])
        with path.open('a') as f: f.write('ial\n')
        self.wait_for(lambda: len(seen)==1)
        path.rename(path.with_suffix('.old'))
        time.sleep(.04)
        path.write_text('new replacement bigger than previous file\n')
        self.wait_for(lambda: len(seen)==2)
        path.write_text('tiny\n')
        self.wait_for(lambda: len(seen)==3)
        self.assertEqual(seen, ['partial\n','new replacement bigger than previous file\n','tiny\n'])
        self.assertTrue(thread.is_alive())

    def wait_for(self, condition):
        until = time.monotonic()+2
        while not condition() and time.monotonic()<until: time.sleep(.01)
        self.assertTrue(condition())


if __name__ == '__main__':
    unittest.main()
