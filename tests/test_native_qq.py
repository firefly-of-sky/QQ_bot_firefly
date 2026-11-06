import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

from scripts.cloud.observe_native_qq import advance, collect, probe_status, DURATION


class NativeProbeTests(unittest.TestCase):
    def test_missing_stale_future_and_title_only_probes_are_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'probe.json'
            self.assertEqual(probe_status(p,1000)['state'],'unknown')
            for value in (
                {'checked_at':800,'state':'offline','evidence':'visual'},
                {'checked_at':1001,'state':'online','evidence':'visual'},
                {'checked_at':1000,'state':'online','window_title':'PRIVATE_CHAT'},
                {'checked_at':1000,'state':'online','evidence':'window_title'},
            ):
                p.write_text(json.dumps(value))
                self.assertEqual(probe_status(p,1000)['state'],'unknown')

    def test_valid_probe_is_whitelisted_and_requires_fresh_login_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'probe.json'
            p.write_text(json.dumps({'checked_at':1000,'state':'online','evidence':'visual',
                'login_confirmed_at':999,'window_title':'PRIVATE_CHAT','uin':'PRIVATE_ACCOUNT',
                'qrcode':'PRIVATE_URL'}))
            value=probe_status(p,1000)
            self.assertEqual(value,dict(checked_at=1000,state='online',fresh=True,
                evidence='visual',login_confirmed_at=999))


class NativeObservationTests(unittest.TestCase):
    def process(self,**changes):
        return dict(state='running',started_at='2026-11-06T00:00:00Z',oom_killed=False)|changes
    def online(self,now,confirmed=False):
        row=dict(state='online',fresh=True,checked_at=now,evidence='visual')
        if confirmed:row['login_confirmed_at']=now
        return row
    def start(self,now=1000):
        value={};advance(value,self.process(),self.online(now,True),now);return value

    def test_process_running_does_not_mean_qq_online_or_start_timer(self):
        value={}
        advance(value,self.process(),dict(state='unknown',fresh=False),1000)
        self.assertEqual(value['outcome'],'WAITING_LOGIN')
        advance(value,self.process(),self.online(1060),1060)
        self.assertEqual(value['outcome'],'WAITING_LOGIN')
        advance(value,self.process(),self.online(2000,True),2000)
        self.assertEqual(value['outcome'],'OBSERVING')
        self.assertEqual(value['started_at'],2000)
        self.assertTrue(value['coverage_verified'])

    def test_fresh_online_samples_can_complete_24_hours(self):
        saved=self.start()
        for now in range(1060,1000+DURATION+1,60):
            advance(saved,self.process(),self.online(now),now)
        self.assertEqual(saved['outcome'],'ONLINE_WINDOW_COMPLETED')
        self.assertEqual(saved['elapsed_seconds'],DURATION)
        self.assertEqual(saved['unknown_samples'],0)
        self.assertEqual(saved['max_observation_gap_seconds'],60)

    def test_missing_probe_is_not_offline_and_makes_completion_unverified(self):
        saved=self.start()
        advance(saved,self.process(),dict(state='unknown',fresh=False),1060)
        self.assertEqual(saved['outcome'],'OBSERVING')
        for now in range(1120,1000+DURATION+1,60):
            advance(saved,self.process(),self.online(now),now)
        self.assertEqual(saved['outcome'],'OBSERVATION_ENDED_UNVERIFIED')
        self.assertEqual(saved['unknown_samples'],1)

    def test_large_sampling_gap_and_clock_regression_invalidate_coverage(self):
        for next_time in (2000,999):
            saved=self.start()
            advance(saved,self.process(),self.online(next_time),next_time)
            self.assertFalse(saved['coverage_verified'])
        saved=self.start()
        advance(saved,self.process(),self.online(1000+DURATION),1000+DURATION)
        self.assertEqual(saved['outcome'],'OBSERVATION_ENDED_UNVERIFIED')

    def test_actual_offline_and_process_failures_end_without_recovery(self):
        saved=self.start()
        advance(saved,self.process(),dict(state='offline',fresh=True),1060)
        self.assertEqual(saved['outcome'],'QQ_OFFLINE')
        # Later login does not erase this observation's evidence.
        advance(saved,self.process(),self.online(1120,True),1120)
        self.assertEqual(saved['outcome'],'QQ_OFFLINE')
        saved=self.start()
        advance(saved,self.process(state='exited'),self.online(1060),1060)
        self.assertEqual(saved['outcome'],'PROCESS_FAILED')
        saved=self.start()
        advance(saved,self.process(started_at='2026-11-06T00:01:00Z'),self.online(1060),1060)
        self.assertEqual(saved['outcome'],'PROCESS_RESTARTED')


class NativeCollectorTests(unittest.TestCase):
    def test_only_read_only_docker_calls_private_logs_and_safe_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'run';state.mkdir()
            (state/'native-qq-probe.json').write_text(json.dumps({'checked_at':1000,
                'state':'online','evidence':'visual','login_confirmed_at':1000,
                'window_title':'PRIVATE_TITLE','qrcode':'PRIVATE_URL'}))
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                self.assertIn(args[1],('inspect','logs'))
                self.assertEqual(args[-1],'qqbot-native-qq-1')
                if args[1]=='inspect':
                    return NS(stdout=json.dumps({'Status':'running','StartedAt':'2026-11-06T00:00:00Z',
                        'OOMKilled':False,'Error':'PRIVATE_ERROR'}),returncode=0,timed_out=False)
                return NS(stdout='PRIVATE_LOG_BODY',returncode=0,timed_out=False,truncated=False,bytes_seen=16)
            result=collect(root,now=1000,run=run)
            self.assertEqual(result['outcome'],'OBSERVING')
            summary=json.dumps(result)
            for text in ('PRIVATE_TITLE','PRIVATE_URL','PRIVATE_ERROR','PRIVATE_LOG_BODY'):
                self.assertNotIn(text,summary)
            p=state/'native-qq-observation.json'
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            folder=state/'native-qq-logs'
            self.assertEqual(folder.stat().st_mode&0o777,0o700)
            sample=folder/result['last_sample'];self.assertEqual(sample.stat().st_mode&0o777,0o600)
            with gzip.open(sample,'rt') as f:
                self.assertEqual(json.load(f)['log']['text'],'PRIVATE_LOG_BODY')
            collect(root,now=1060,run=run)
            self.assertIn('1970-01-01T00:16:40+00:00',calls[-1])
            self.assertFalse((state/'maintenance').exists())

    def test_summary_does_not_create_runtime_directory(self):
        script=Path(__file__).parents[1]/'scripts/cloud/observe_native_qq.py'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'does-not-exist'
            result=subprocess.run([sys.executable,str(script),'--root',str(root),'--summary'],
                                  capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout),{'outcome':'NOT_STARTED'})
            self.assertFalse(root.exists())

    def test_unavailable_container_or_log_command_is_not_qq_identity_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(args,**kwargs):
                return NS(stdout='',returncode=1,timed_out=False,truncated=False,bytes_seen=0)
            result=collect(tmp,now=1000,run=run)
            self.assertEqual(result['outcome'],'WAITING_LOGIN')
            self.assertEqual(result['latest']['container']['state'],'unknown')
            self.assertFalse(result['log_collection_ok'])
            self.assertNotIn('logs_until',result)


class AccessibilityFeatureTests(unittest.TestCase):
    def test_fixed_candidate_flags_never_print_arbitrary_names_or_infer_online(self):
        from scripts.cloud.native_qq_accessibility import summarize_records
        row=summarize_records([
            ('push button','登录'),('label','请使用手机QQ扫码登录'),
            ('label','你的账号当前登录已失效，请重新登录。'),
            ('entry','搜索'),('push button','消息'),('push button','联系人'),
            ('push button','在线状态 在线'),('frame','PRIVATE_WINDOW_TITLE'),
            ('text','PRIVATE_CHAT_ACCOUNT_AND_SECRET'),
        ])
        self.assertTrue(row['features']['login_button'])
        self.assertTrue(row['features']['qr_login_label'])
        self.assertTrue(row['features']['identity_invalid_notice'])
        self.assertTrue(row['features']['online_status_control'])
        self.assertNotIn('PRIVATE_',json.dumps(row))
        self.assertNotIn('state',row)

    def test_qq_filter_is_exact_and_walk_is_bounded(self):
        from scripts.cloud.native_qq_accessibility import qq_application_name,walk_application
        self.assertTrue(qq_application_name('QQ'))
        self.assertFalse(qq_application_name('not-QQ'))
        class Node:
            name='PRIVATE_WINDOW';childCount=100000
            def getRoleName(self):return 'panel'
            def getChildAtIndex(self,index):return self
        row=walk_application(Node(),max_nodes=20,deadline=100,clock=lambda:0)
        self.assertLessEqual(row['nodes_seen']+row['nodes_failed'],20)
        self.assertTrue(row['traversal_truncated'])
        self.assertNotIn('PRIVATE_WINDOW',json.dumps(row))

    def test_online_text_and_qq_application_are_only_candidates(self):
        from scripts.cloud.native_qq_accessibility import collect_features
        class Node:
            def __init__(self,name,role,children=()):self.name=name;self.role=role;self.children=children;self.childCount=len(children)
            def getRoleName(self):return self.role
            def getChildAtIndex(self,index):return self.children[index]
        desktop=Node('PRIVATE_DESKTOP','desktop',[
            Node('QQ','application',[Node('在线','push button')]),
            Node('PRIVATE_OTHER_APP','application',[Node('PRIVATE_OTHER_CHAT','label')]),
        ])
        row=collect_features(registry=NS(getDesktop=lambda _:desktop),clock=lambda:0)
        self.assertTrue(row['qq_application_found'])
        self.assertTrue(row['features']['online_status_control'])
        self.assertEqual(row['state'],'unknown')
        self.assertEqual(row['qq_application_count'],1)
        self.assertNotIn('PRIVATE_',json.dumps(row))


class AccessibilityExtendedFeatureTests(unittest.TestCase):
    def test_descriptions_and_checked_states_are_summarized_without_content(self):
        from scripts.cloud.native_qq_accessibility import summarize_extended
        row=summarize_extended([
            ('push button','PRIVATE_ACCOUNT的头像','PRIVATE_DESCRIPTION',False,False,True,(0,1)),
            ('toggle button','在线状态','当前在线',True,False,True,(0,2)),
            ('label','PRIVATE_CHAT','连接已断开',False,False,False,(0,3)),
        ])
        self.assertEqual(row['fixed_word_counts']['avatar'],1)
        self.assertEqual(row['fixed_word_counts']['online'],1)
        self.assertEqual(row['fixed_word_counts']['connection_lost'],1)
        self.assertTrue(row['controls'][1]['checked'])
        self.assertEqual(row['controls'][1]['path'],[0,2])
        self.assertNotIn('PRIVATE_',json.dumps(row))
        self.assertNotIn('state',row)

    def test_extended_walk_uses_state_set_and_remains_unknown(self):
        from scripts.cloud.native_qq_accessibility import collect_features
        class Node:
            description='PRIVATE_DESCRIPTION'
            def __init__(self,name,role,children=()):self.name=name;self.role=role;self.children=children;self.childCount=len(children)
            def getRoleName(self):return self.role
            def getChildAtIndex(self,index):return self.children[index]
            def getState(self):return NS(contains=lambda flag:flag=='CHECKED')
        desktop=Node('PRIVATE_DESKTOP','desktop',[Node('QQ','application',[
            Node('PRIVATE_ACCOUNT的头像','push button'),Node('在线状态','toggle button')])])
        row=collect_features(registry=NS(getDesktop=lambda _:desktop),extended=True,
                             state_ids=dict(checked='CHECKED',selected='SELECTED'),clock=lambda:0)
        self.assertEqual(row['state'],'unknown')
        self.assertTrue(row['extended_apps'][0]['controls'][0]['state_available'])
        self.assertTrue(row['extended_apps'][0]['controls'][0]['checked'])
        self.assertFalse(row['extended_apps'][0]['controls'][0]['selected'])
        self.assertNotIn('PRIVATE_',json.dumps(row))


class AccessibilityNumericDetailTests(unittest.TestCase):
    def test_only_fixed_numeric_and_state_fields_are_returned(self):
        from scripts.cloud.native_qq_accessibility import numeric_control_details
        class DBusInt(int):pass
        class Node:
            def getState(self):return NS(contains=lambda x:x=='SHOWING')
            def queryComponent(self):return NS(getExtents=lambda coord:(DBusInt(20),DBusInt(30),DBusInt(50),DBusInt(40)))
            def queryValue(self):return NS(currentValue=1.0,minimumValue=0.0,maximumValue=2.0)
        row=numeric_control_details(Node(),dict(showing='SHOWING',visible='VISIBLE'))
        self.assertEqual(row,dict(showing=True,visible=False,
            screen_bbox=dict(x=20,y=30,width=50,height=40),
            numeric_value=dict(current=1.0,minimum=0.0,maximum=2.0)))

    def test_invalid_values_and_private_interface_errors_are_not_emitted(self):
        from scripts.cloud.native_qq_accessibility import numeric_control_details
        class Node:
            def getState(self):raise ValueError('PRIVATE_WINDOW')
            def queryComponent(self):return NS(getExtents=lambda coord:NS(x=float('nan'),y=0,width=1,height=1))
            def queryValue(self):return NS(currentValue='PRIVATE_TEXT',minimumValue=0,maximumValue=1)
        row=numeric_control_details(Node(),dict(showing='SHOWING',visible='VISIBLE'))
        self.assertEqual(row,{})


class NativeGuiProbeTests(unittest.TestCase):
    def snapshot(self):
        return dict(qq_application_found=True,nodes_failed=0,traversal_truncated=False,
            features=dict(search_input=True,message_navigation=True,contacts_navigation=True),
            extended_apps=[dict(fixed_word_counts={},controls=[dict(
                name_flags=dict(status_control=True),description_flags={},
                status_details=dict(showing=True,visible=True,screen_bbox=dict(x=83,y=78,width=10,height=10)))])])
    def test_green_pixel_count_uses_calibrated_threshold(self):
        from scripts.cloud.probe_native_qq import green_pixel_count
        rgb=bytes((0,150,30,40,110,20,120,140,110,0,10,200,200,200,200))
        self.assertEqual(green_pixel_count(rgb),1)
        with self.assertRaises(ValueError):green_pixel_count(b'xx')
        with self.assertRaises(ValueError):green_pixel_count(bytes(64*64*3+3))
    def test_green_main_gui_is_online_but_no_green_does_not_mean_offline(self):
        from scripts.cloud.probe_native_qq import classify
        view=self.snapshot()
        self.assertEqual(classify(view,[22],login_confirmed=True),'online')
        self.assertEqual(classify(view,[0],login_confirmed=True),'unknown')
        self.assertEqual(classify(view,[],login_confirmed=True),'unknown')
        view['traversal_truncated']=True
        self.assertEqual(classify(view,[22],login_confirmed=True),'unknown')
    def test_login_view_needs_prior_confirmation_and_chat_notice_is_not_offline(self):
        from scripts.cloud.probe_native_qq import classify
        view=self.snapshot();view['features']['identity_invalid_notice']=True
        self.assertEqual(classify(view,[22],login_confirmed=True),'unknown')
        self.assertEqual(classify(view,[22],login_confirmed=True,explicit_invalid_dialog=True),'offline')
        view['features']=dict(login_button=True)
        self.assertEqual(classify(view,[],login_confirmed=False),'unknown')
        self.assertEqual(classify(view,[],login_confirmed=True),'offline')
        view['features']=dict(qr_image=True)
        self.assertEqual(classify(view,[],login_confirmed=True),'offline')
    def test_reconnect_or_partial_scan_is_never_classified_online(self):
        from scripts.cloud.probe_native_qq import classify
        view=self.snapshot();view['extended_apps'][0]['fixed_word_counts']['reconnecting']=1
        self.assertEqual(classify(view,[22]),'unknown')
        view=self.snapshot();view['nodes_failed']=1
        self.assertEqual(classify(view,[22]),'unknown')
    def test_only_visible_small_status_control_is_captured_and_output_is_safe(self):
        from scripts.cloud.probe_native_qq import probe,status_rectangles,capture_green
        view=self.snapshot();duplicate=dict(name_flags=dict(status_control=True),description_flags={},
                status_details=dict(showing=True,visible=True,screen_bbox=dict(x=223,y=130,width=19,height=18)))
        view['extended_apps'][0]['controls'].append(duplicate)
        self.assertEqual(status_rectangles(view),[(83,78,10,10)])
        seen=[]
        def capture(rect):seen.append(rect);return 22
        row=probe(confirmed_at=900,now=1000,snapshot_reader=lambda **kw:view,capture=capture)
        self.assertEqual(row['state'],'online');self.assertEqual(row['indicators']['green_pixels'],22)
        self.assertNotIn('screen_bbox',json.dumps(row));self.assertNotIn('name_flags',json.dumps(row))
        self.assertEqual(row['scope'],'native_qq_gui_display_only')
        def run(args,**kw):
            self.assertNotIn('-y',args);self.assertEqual(args[-1],'-')
            return NS(returncode=0,stdout=bytes((0,150,30))*100)
        self.assertEqual(capture_green((83,78,10,10),display=':1',run=run),100)


if __name__=='__main__':unittest.main()

class DockerTimestampTests(unittest.TestCase):
    def test_docker_nanosecond_timestamp_is_validated_for_ubuntu_python(self):
        from scripts.cloud.observe_native_qq import container_status
        def run(*args, **kwargs):
            return NS(returncode=0,timed_out=False,stdout=json.dumps({
                'Status':'running','StartedAt':'2026-11-06T10:24:49.987654321Z','OOMKilled':False}))
        self.assertEqual(container_status(run)['started_at'],'2026-11-06T10:24:49.987654321Z')

class CancelledObservationTests(unittest.TestCase):
    def test_manual_cancel_is_terminal_and_not_relabelled_as_qq_failure(self):
        d={'outcome':'CANCELLED_BY_OPERATOR','finished_at':1000}
        advance(d,{'state':'exited'},{'state':'offline'},1060)
        self.assertEqual(d['outcome'],'CANCELLED_BY_OPERATOR')

class ProbeEvidencePrivacyTests(unittest.TestCase):
    def test_gui_indicators_preserve_fixed_numbers_only(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'probe.json'
            p.write_text(json.dumps({'checked_at':1000,'state':'online','evidence':'visual',
                'scope':'native_qq_gui_display_only','indicators':{'green_pixels':22,
                'main_navigation':True,'chat':'PRIVATE','status_regions_sampled':999999}}))
            d=probe_status(p,1000)
            self.assertEqual(d['indicators'],{'green_pixels':22,'main_navigation':True})
            self.assertNotIn('PRIVATE',json.dumps(d))
