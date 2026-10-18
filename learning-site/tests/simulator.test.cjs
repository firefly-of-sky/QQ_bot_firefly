const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context=vm.createContext({});
for(const file of ['data.js','simulator.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',file),'utf8'),context);
const A=context.Academy;
const enter=s=>s.run('cd '+A.project);
const command=(s,a)=>s.run(A.remote(a));

test('remote commands require the correct Mac directory; failure does not advance tasks',()=>{
  const s=new A.Simulator('scan');assert.match(command(s,'status'),/No such file/);assert.equal(s.events.has('status'),false);
  enter(s);assert.match(command(s,'status'),/waiting_qrcode/);assert.equal(s.ready,false);
});
test('SSH shell and wrapper preserve distinct local and remote contexts',()=>{
  const s=new A.Simulator();enter(s);s.run('ssh -F .deployment/ssh_config qqbot-cloud');
  assert.equal(s.terminal.remote,true);assert.equal(s.run('pwd'),'/home/learner');
  assert.match(command(s,'stop'),/先输入 exit/);assert.equal(s.running,true);
  s.run('exit');assert.equal(s.terminal.remote,false);assert.equal(s.terminal.cwd,A.project);
});
test('complete QQ recovery requires tunnel, phone confirmation, second terminal, and verification',()=>{
  const s=new A.Simulator('scan');enter(s);command(s,'status');
  assert.match(command(s,'configure-login'),/尚未成功登录/);assert.equal(s.events.has('configure-login'),false);
  assert.match(s.scan(),/先在 Mac/);assert.equal(s.logged,false);
  command(s,'webui');assert.equal(s.tunnel,true);
  assert.match(command(s,'status'),/正在维持隧道/);
  s.scan();s.openSecond();assert.notEqual(s.terminal.cwd,A.project);
  enter(s);command(s,'configure-login');command(s,'status');s.message();
  s.active=0;s.interrupt();assert.equal(s.tunnel,false);assert.equal(s.running,true);assert.equal(s.ready,true);
  for(const [event] of A.lessons[4].tasks) assert.ok(s.events.has(event),event);
});
test('ready status never proves model availability',()=>{
  const s=new A.Simulator('model');enter(s);assert.match(command(s,'status'),/"ready": true/);
  assert.match(s.message(),/模型服务请求失败/);assert.equal(s.events.has('message'),false);
});
test('stopped services cannot pass QQ login; start and backup are separate actions',()=>{
  const s=new A.Simulator('stopped');enter(s);command(s,'status');assert.equal(s.ready,false);
  command(s,'webui');assert.match(s.scan(),/服务未运行/);s.interrupt();
  command(s,'start');command(s,'status');command(s,'backup');s.message();
  for(const [event] of A.lessons[5].tasks) assert.ok(s.events.has(event),event);
});
test('SSH timeout and busy port fail without claiming connection or tunnel success',()=>{
  const s=new A.Simulator('ssh');enter(s);assert.match(command(s,'status'),/timed out/);assert.equal(s.events.has('status'),false);
  s.run('ssh -F .deployment/ssh_config qqbot-cloud');assert.equal(s.terminal.remote,false);
  const p=new A.Simulator('port');enter(p);assert.match(command(p,'webui'),/Address already in use/);assert.equal(p.tunnel,false);
});
test('arbitrary commands are not evaluated or executed',()=>{
  const s=new A.Simulator();s.run('globalThis.injected = true');s.run('rm -rf /');
  assert.equal(context.injected,undefined);assert.equal(s.running,true);assert.equal(s.events.size,0);
});
test('all seven lesson task lists can be completed in the teaching environment',()=>{
  A.lessons.forEach((l,i)=>{
    const s=new A.Simulator(l.scenario||'healthy');s.run('pwd');enter(s);s.run('ls');command(s,'status');
    if(i===2){s.run('ssh -F .deployment/ssh_config qqbot-cloud');s.run('pwd');s.run('exit');}
    if(i===4||i===6){command(s,'webui');s.scan();s.openSecond();enter(s);command(s,'configure-login');command(s,'status');s.message();s.active=0;s.interrupt();}
    if(i===5){command(s,'start');command(s,'status');command(s,'backup');s.message();}
    for(const [event] of l.tasks) assert.ok(s.events.has(event),`lesson ${i+1}: ${event}`);
  });
});
test('course data has complete lessons and valid quiz answers',()=>{
  assert.equal(A.lessons.length,7);
  for(const l of A.lessons){assert.ok(l.real.length&&l.blocks.length&&l.tasks.length);for(const [q,opts,answer,why] of l.quiz){assert.ok(q&&why);assert.ok(answer>=0&&answer<opts.length);}}
});
