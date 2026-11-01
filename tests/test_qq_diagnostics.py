import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace as NS
import unittest

from scripts.cloud.napcat_diagnostics import patch_bundle,HEADER,CALLBACK
from scripts.cloud.ops import Operations
from scripts.cloud.observe_qq import observe,resume


class NativeDiagnosticsTests(unittest.TestCase):
    def fixture(self):
        return '''class Core {
''' + HEADER + '''
    e.onKickedOffLine = (r) => {
      const i = `[KickedOffLine] [${r.tipsTitle}] ${r.tipsDesc}`;
      ''' + CALLBACK + '''
    };
    this.listener = e;
  }
}
'''

    def test_unknown_program_is_rejected_and_patch_is_idempotent(self):
        source=self.fixture();digest=hashlib.sha256(source.encode()).hexdigest()
        with self.assertRaises(ValueError):patch_bundle(source)
        patched=patch_bundle(source,digest)
        self.assertEqual(patched,patch_bundle(patched,digest))
        with self.assertRaises(ValueError):patch_bundle(patched+'\n',digest)

    def test_reviewed_41842_profile_uses_its_actual_callback_parameter(self):
        # Exercise the second profile without weakening production SHA pinning.
        from unittest.mock import patch
        import scripts.cloud.napcat_diagnostics as diagnostics
        source=self.fixture().replace('EM()', 'X9()').replace('(r) =>', '(n) =>')
        source=source.replace('r.tipsTitle', 'n.tipsTitle').replace('r.tipsDesc', 'n.tipsDesc')
        digest=hashlib.sha256(source.encode()).hexdigest()
        with patch.object(diagnostics, 'SOURCE_41842_SHA256', digest):
            patched=patch_bundle(source)
            self.assertEqual(patch_bundle(patched), patched)
        self.assertIn('Number.isSafeInteger(n.kickedType)', patched)
        self.assertNotIn('r.kickedType', patched)
        with self.assertRaises(ValueError):patch_bundle(patched+'changed')

    @unittest.skipUnless(shutil.which('node'),'Node is needed to execute the native callback fixture')
    def test_real_javascript_callback_preserves_logout_and_excludes_private_fields(self):
        source=self.fixture();patched=patch_bundle(source,hashlib.sha256(source.encode()).hexdigest())
        program='const EM=class {};\n'+patched+'''
const diagnostics=[],emitted=[];
const core=new Core();
core.context={logger:{logError(){},logWarn(line){diagnostics.push(JSON.parse(line.split("[QQbotDiag/v1] ")[1]));}}};
core.selfInfo={online:true};core.event={emit(...args){emitted.push(args);}};
(async()=>{await core.initNapCatCoreListeners();core.listener.onKickedOffLine({
 appId:123,instanceId:456,sameDevice:false,kickedType:0,securityKickedType:2,
 tipsTitle:"offline",tipsDesc:"system notice",cookie:"MUST_NOT_CAPTURE",uin:"PRIVATE_ACCOUNT"});
 console.log(JSON.stringify({diagnostics,emitted,online:core.selfInfo.online}));})();
'''
        result=subprocess.run(['node','-e',program],capture_output=True,text=True,check=True)
        parsed=json.loads(result.stdout);row=parsed['diagnostics'][0]
        self.assertFalse(parsed['online'])
        self.assertEqual(parsed['emitted'],[['KickedOffLine','[KickedOffLine] [offline] system notice']])
        self.assertEqual(row['kickedType'],0);self.assertEqual(row['securityKickedType'],2)
        self.assertFalse(row['sameDevice'])
        self.assertNotIn('MUST_NOT_CAPTURE',result.stdout);self.assertNotIn('PRIVATE_ACCOUNT',result.stdout)


class IncidentTests(unittest.TestCase):
    def test_identity_snapshot_handles_official_linux_global_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(args, **kwargs):
                if 'logs' in args:return NS(stdout='', stderr='', returncode=0)
                if '/nt_qq/global/' in args[-1]:return NS(stdout='', returncode=1)
                return NS(stdout='a'*64+'  /fixed/path\n', returncode=0)
            operation=Operations(tmp,run=run)
            result=operation.diagnose({})
            row=json.loads((operation.state/'incidents'/result['incident']).read_text())
            self.assertIn('machine-info-linux', row['identity_sha256'])
            self.assertNotIn('machine-info-legacy', row['identity_sha256'])

    def test_private_bounded_snapshot_and_safe_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            def run(args,**kwargs):
                if 'logs' in args:
                    return NS(stdout='private fixture text\n[QQbotDiag/v1] '+json.dumps({
                        'event':'qq_kicked_offline','kickedType':0,'securityKickedType':2,
                        'sameDevice':False,'cookie':'not-a-diagnostic-field'}),stderr='',returncode=0)
                self.assertIn('sha256sum',args)
                return NS(stdout='a'*64+'  /fixed/path\n',returncode=0)
            operation=Operations(tmp,run=run)
            result=operation.diagnose({'application':{'ready':False}})
            self.assertEqual(result['native_kick_records'],1)
            self.assertNotIn('private fixture',json.dumps(result))
            target=Path(tmp)/'run/incidents'/result['incident']
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertEqual(target.parent.stat().st_mode&0o777,0o700)
            data=json.loads(target.read_text())
            self.assertEqual(data['native_kicks'],[{'kickedType':0,'securityKickedType':2,'sameDevice':False}])
            self.assertIn('private fixture',data['napcat_log_tail'])

    def test_offline_is_captured_once_until_a_real_online_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp);calls=[]
            operation.diagnose=lambda status:calls.append(status) or {'incident':'fixture'}
            saved={};offline={'application':{'reason':'qq_connection_pending'}}
            self.assertTrue(operation.observe_qq(offline,saved))
            self.assertFalse(operation.observe_qq(offline,saved))
            self.assertFalse(operation.observe_qq({'application':{'reason':'probe_unavailable'}},saved))
            operation.observe_qq({'application':{'qq_connected':True,'qq_online':True}},saved)
            self.assertTrue(operation.observe_qq(offline,saved))
            self.assertEqual(len(calls),2)

    def test_watchdog_captures_evidence_without_restarting_healthy_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp,run=lambda *a,**k:self.fail('Unexpected mutation'))
            (operation.state/'qq-diagnostics.enabled').touch()
            status=dict(maintenance=False,services={'bot':{'state':'running','health':'healthy'},
                'napcat':{'state':'running'}},application={'reason':'qq_connection_pending'},disk_used_percent=20,
                qq_login={'phase':'waiting_qrcode'})
            operation.status=lambda:status
            operation.diagnose=lambda status:{'incident':'fixture'}
            self.assertEqual(operation.watchdog()['action'],['qq_incident_captured','qq_scan_required'])
            self.assertEqual(operation.watchdog()['action'],['qq_scan_required'])


class IsolationTests(unittest.TestCase):
    def run_case(self,statuses):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        clock=[0];calls=[]
        operation=Operations(tmp.name)
        rows=iter([{'isLogin':True}]+statuses)
        operation.napcat_api=lambda:lambda _:next(rows)
        operation.compose=lambda *args:calls.append(args)
        operation.diagnose=lambda status:{'incident':'captured'}
        result=observe(operation,60,now=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        return operation,calls,result

    def test_qq_can_fail_without_bot_and_bot_always_resumes(self):
        operation,calls,result=self.run_case([{'isLogin':True},{'isLogin':False}])
        self.assertEqual(result['outcome'],'QQ_OFFLINE_WITHOUT_BOT')
        self.assertTrue(result['bot_resumed']);self.assertFalse((operation.state/'maintenance').exists())
        self.assertEqual(calls[0],('stop','bot'));self.assertEqual(calls[-1][-1],'bot')

    def test_timeout_is_an_observation_not_a_claim_bot_caused_it(self):
        operation,calls,result=self.run_case([{'isLogin':True}]*3)
        self.assertEqual(result['outcome'],'QQ_STILL_ONLINE_WITHOUT_BOT')
        self.assertTrue(result['bot_resumed']);self.assertEqual(result['elapsed_seconds'],60)

    def test_other_operator_maintenance_is_never_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp,run=lambda *a,**k:self.fail('Unexpected Docker mutation'))
            marker=operation.state/'maintenance';marker.write_text('other-operator')
            self.assertFalse(resume(operation,{'lease':'this-observation'}))
            self.assertEqual(marker.read_text(),'other-operator')
            with self.assertRaises(RuntimeError):observe(operation,60)
