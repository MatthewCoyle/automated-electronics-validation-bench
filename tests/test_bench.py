from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from validationbench.device import SimulatedDUT, CommunicationError
from validationbench.model import Limits, Measurement, Plan, Scenario, load_plan, strict
from validationbench.runner import evaluate, run, summarize
from validationbench.report import write_reports

ROOT = Path(__file__).resolve().parents[1]
def scenario(**kw):
    return Scenario(**(dict(name="test", load_a=.5, initial_temperature_c=25, expect_shutdown=False) | kw))
def plan(*scenarios):
    return Plan("test", tuple(scenarios) or (scenario(),))


class ModelTests(unittest.TestCase):
    def test_numeric_rejections(self):
        for value in (float('nan'),float('inf'),-1, True, '1',10**400):
            with self.subTest(value=value), self.assertRaises(ValueError): scenario(load_a=value)

    def test_all_scenario_constraints(self):
        for field,value in [('name',''),('samples',0),('samples',True),('samples',10001),('interval_s',0),('initial_temperature_c',151),('ambient_c',126),('expect_shutdown',1),('fault','unknown'),('description',3)]:
            with self.subTest(field=field), self.assertRaises(ValueError): scenario(**{field:value})

    def test_limits_inverted_and_nonfinite(self):
        for kw in ({'voltage_min_v':6},{'temperature_min_c':126},{'off_max_v':float('nan')},{'current_error_a':-1}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): Limits(**kw)

    def test_unknown_and_missing_fields(self):
        for data in ({'surprise':1},{},[],None):
            with self.subTest(data=data),self.assertRaises(ValueError): strict(Scenario,data)

    def test_plan_invalid(self):
        for args in [('x',()),('x',(scenario(),scenario())),('',(scenario(),))]:
            with self.subTest(args=args),self.assertRaises(ValueError): Plan(*args)
        with self.assertRaises(ValueError): Plan('x',(scenario(),),schema_version=True)
        for kw in ({'scenarios':(None,)},{'scenarios':(scenario(),),'limits':None},{'scenarios':tuple(scenario(name=str(i),samples=10000) for i in range(3))}):
            with self.assertRaises(ValueError): Plan(name='x',**kw)

    def test_json_duplicate_nan_and_shape(self):
        for text in ('{"scenarios":[],"scenarios":[]}','{"scenarios":[],"x":NaN}','[]','{"scenarios":{}}'):
            with TemporaryDirectory() as td:
                p=Path(td)/'p.json';p.write_text(text)
                with self.assertRaises(ValueError): load_plan(p)

    def test_plan_digest_tracks_limits_and_stimulus(self):
        p=plan();self.assertEqual(p.digest,plan().digest)
        self.assertNotEqual(p.digest,replace(p,limits=Limits(off_max_v=.04)).digest)
        self.assertNotEqual(p.digest,plan(scenario(load_a=.6)).digest)


class SimulatorTests(unittest.TestCase):
    def sample(self,s):
        d=SimulatedDUT();d.connect();d.configure(s);return d,d.sample(s.interval_s)

    def test_regulation_and_current(self):
        _,m=self.sample(scenario(load_a=1));self.assertEqual(m.voltage_v,4.93);self.assertEqual(m.current_a,1)

    def test_protection_boundaries(self):
        for t,load,off in [(79.999,1,False),(80,1,True),(25,1.999,False),(25,2,True),(100,3,True)]:
            with self.subTest(t=t,load=load):
                _,m=self.sample(scenario(initial_temperature_c=t,load_a=load))
                self.assertEqual(m.shutdown,off)
                if off:self.assertEqual((m.voltage_v,m.current_a),(0,0))

    def test_thermal_evolution_and_latch(self):
        d,m=self.sample(scenario(initial_temperature_c=80,ambient_c=25))
        for _ in range(1000): m=d.sample(.1)
        self.assertLess(m.temperature_c,26);self.assertTrue(m.shutdown)
        d.configure(scenario());self.assertFalse(d.sample(.1).shutdown)

    def test_temperature_heats_toward_equilibrium(self):
        d,m=self.sample(scenario(load_a=1))
        for _ in range(1000):m=d.sample(.1)
        self.assertAlmostEqual(m.temperature_c,33,delta=.06)

    def test_sensor_bias_does_not_change_physical_heating(self):
        healthy,_=self.sample(scenario());biased,_=self.sample(scenario(fault='current_bias'))
        for _ in range(20):
            normal=healthy.sample(.1);bad=biased.sample(.1)
        self.assertEqual(normal.temperature_c,bad.temperature_c)
        self.assertAlmostEqual(bad.current_a-normal.current_a,.2)

    def test_lifecycle(self):
        d=SimulatedDUT()
        with self.assertRaises(CommunicationError):d.sample(.1)
        with self.assertRaises(CommunicationError):d.configure(scenario())
        d.connect();d.configure(scenario());d.safe_shutdown();self.assertFalse(d.enabled)
        d.close();self.assertFalse(d.connected)

    def test_adapter_rejects_invalid_inputs(self):
        d=SimulatedDUT()
        with self.assertRaises(ValueError):d.configure(None)
        for interval in (-1,True,float('nan'),float('inf')):
            with self.assertRaises(ValueError):d.sample(interval)

    def test_timeout_does_not_advance_virtual_time(self):
        d=SimulatedDUT();d.connect();d.configure(scenario(fault='timeout_once'))
        with self.assertRaises(TimeoutError):d.sample(.1)
        self.assertEqual(d.sample(.1).time_s,0)


class RunnerTests(unittest.TestCase):
    def test_acceptance(self):
        results=run(load_plan(ROOT/'plans/acceptance.json'))
        self.assertEqual(summarize(results),{'total':7,'pass':7,'fail':0,'error':0})

    def test_injected_defects_identified(self):
        results=run(load_plan(ROOT/'plans/injected_faults.json'))
        self.assertEqual([r['status'] for r in results],['PASS','FAIL','FAIL','FAIL'])
        self.assertTrue(any(c['name'].endswith('.current_a') and not c['passed'] for c in results[2]['checks']))

    def test_recovery_and_error_separation(self):
        results=run(load_plan(ROOT/'plans/recovery.json'))
        self.assertEqual([r['status'] for r in results],['PASS','ERROR','ERROR','ERROR','PASS'])
        self.assertEqual(len(results[0]['events']),1)
        self.assertEqual(results[1]['measurements'],[])

    def test_exact_acceptance_limits(self):
        s=scenario(samples=1)
        for v,expected in [(4.75,True),(5.25,True),(4.74999,False),(5.25001,False)]:
            checks=evaluate(s,Limits(),[Measurement(0,v,.5,25,False,'none')])
            self.assertEqual(all(c['passed'] for c in checks),expected)

    def test_no_samples_never_passes(self):
        self.assertFalse(evaluate(scenario(),Limits(),[])[0]['passed'])

    def test_current_temperature_and_off_voltage_boundaries(self):
        s=scenario(samples=1)
        for current,temp,expected in [(.5,125,True),(.5,-40,True),(.5,125.001,False),(.5,-40.001,False),(.48,25,True),(.52,25,True),(.52000001,25,False),(.519,25,True),(.521,25,False)]:
            with self.subTest(current=current,temp=temp):
                self.assertEqual(all(c['passed'] for c in evaluate(s,Limits(),[Measurement(0,5,current,temp,False,'none')])),expected)
        s=scenario(samples=1,expect_shutdown=True)
        for voltage,expected in [(0,True),(.05,True),(-.0001,False),(.0501,False)]:
            self.assertEqual(all(c['passed'] for c in evaluate(s,Limits(),[Measurement(0,voltage,0,25,True,'thermal')])),expected)

    def test_telemetry_rejects_boolean_nonfinite_and_unknown_reason(self):
        for kw in ({'voltage_v':True},{'current_a':float('nan')},{'temperature_c':float('inf')},{'time_s':-1},{'shutdown':0},{'reason':'unknown'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):
                replace(Measurement(0,5,.5,25,False,'none'),**kw).validate()

    def test_cleanup_on_connect_and_configure_failure(self):
        for stage in ('connect','configure'):
            calls=[]
            class Broken(SimulatedDUT):
                def connect(self):
                    if stage=='connect':raise OSError('connect failed')
                    super().connect()
                def configure(self,s):raise RuntimeError('configure failed')
                def safe_shutdown(self):calls.append('safe')
                def close(self):calls.append('close')
            self.assertEqual(run(plan(),Broken)[0]['status'],'ERROR');self.assertEqual(calls,['safe','close'])

    def test_close_runs_if_shutdown_fails(self):
        calls=[]
        class Broken(SimulatedDUT):
            def safe_shutdown(self):raise OSError('unconfirmed output')
            def close(self):calls.append('close')
        result=run(plan(),Broken)[0]
        self.assertEqual(result['status'],'ERROR');self.assertEqual(calls,['close'])

    def test_unexpected_exception_isolated(self):
        class Broken(SimulatedDUT):
            def sample(self,dt):raise RuntimeError('unexpected')
        self.assertEqual(run(plan(),Broken)[0]['status'],'ERROR')

    def test_bad_type_and_timestamp(self):
        for payload in (None,Measurement(.4,5,.5,25,False,'none')):
            class Broken(SimulatedDUT):
                def sample(self,dt):return payload
            result=run(plan(scenario(samples=1)),Broken)[0]
            self.assertEqual(result['status'],'ERROR' if payload is None else 'FAIL')

    def test_deterministic_run(self):
        p=load_plan(ROOT/'plans/acceptance.json');self.assertEqual(run(p),run(p))


class ReportCLITests(unittest.TestCase):
    def test_report_evidence_and_html_escaping(self):
        p=Plan('<script>alert(1)</script>',(scenario(name='<img src=x>'),))
        with TemporaryDirectory() as td:
            write_reports(p,run(p),td)
            doc=(Path(td)/'report.html').read_text(encoding='utf-8')
            self.assertIn('&lt;script&gt;',doc);self.assertNotIn('<img src=x>',doc)
            data=json.loads((Path(td)/'report.json').read_text())
            self.assertEqual(data['plan_sha256'],p.digest);self.assertEqual(len(data['results'][0]['measurements']),5)
            self.assertIn('SIMULATION ONLY',data['mode'])

    def test_cli_exit_codes_and_real_reports(self):
        for filename,expected in [('acceptance',0),('injected_faults',1),('recovery',2),('missing',2)]:
            with self.subTest(filename=filename),TemporaryDirectory() as td:
                proc=subprocess.run([sys.executable,'-m','validationbench','--plan',str(ROOT/'plans'/f'{filename}.json'),'--output-dir',td],cwd=ROOT,capture_output=True,text=True)
                self.assertEqual(proc.returncode,expected,proc.stdout+proc.stderr)
                if filename!='missing':self.assertTrue((Path(td)/'report.html').is_file())


if __name__=='__main__': unittest.main()
