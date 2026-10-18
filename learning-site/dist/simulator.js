Academy.Simulator = class {
  constructor(scenario='healthy') { this.reset(scenario); }
  reset(scenario) {
    this.scenario=scenario; this.running=scenario!=='stopped'; this.logged=scenario!=='scan';
    this.model=scenario!=='model'; this.sshAvailable=scenario!=='ssh'; this.busy=scenario==='port';
    this.tunnel=false; this.tunnelTerminal=null; this.active=0;
    this.terminals=[{remote:false,cwd:'/Users/firefly',localCwd:'/Users/firefly'}];
    this.events=new Set(); this.history=[];
  }
  get terminal() { return this.terminals[this.active]; }
  get ready() { return this.running && this.logged; }
  prompt() { return this.terminal.remote ? `learner@qqbot-cloud:${this.terminal.cwd}$` : `firefly@Mac ${this.terminal.cwd.replace('/Users/firefly','~')} %`; }
  emit(...events) { for(const e of events) this.events.add(e); }
  openSecond() { if(this.terminals.length===1) this.terminals.push({remote:false,cwd:'/Users/firefly',localCwd:'/Users/firefly'}); this.active=1; return '已打开 Mac 终端 B。新窗口从主目录开始，请先进入项目。终端 A 的隧道仍在运行。'; }
  interrupt() {
    if(this.tunnel && this.tunnelTerminal===this.active) { this.tunnel=false; this.tunnelTerminal=null; this.emit('tunnel-closed'); return '^C\n管理隧道已结束。云端机器人没有因此停止。'; }
    return '^C\n已取消本行输入。';
  }
  scan() {
    if(!this.tunnel) return '先在 Mac 项目目录运行 webui，建立模拟管理隧道。';
    if(!this.running) return 'NapCat 服务未运行，请先查看状态并恢复运行。';
    this.logged=true; this.emit('scan'); return '【模拟管理页】手机扫码并确认成功。QQ 已登录。请在第二个 Mac 终端设置快捷登录，再查状态、验证回复。';
  }
  message() {
    if(!this.ready) return '【模拟发消息】未收到回复。先检查状态与 QQ 登录。';
    if(!this.model) return '【模拟发消息】模型服务请求失败。QQ 虽然在线，但回复链路未通过；请检查模型 API、账户与网络。';
    this.emit('message'); return '【模拟发消息】另一个 QQ：你好\n机器人：你好，我已经恢复工作。\n模拟回复链路验证成功。真实 QQ 还需要你亲自测试。';
  }
  status() {
    this.emit('status'); if(this.ready) this.emit('ready-status');
    const value={services:{napcat:{state:this.running?'running':'stopped'},bot:{state:this.running?'running':'stopped'}},application:{live:this.running,ready:this.ready}};
    if(this.running&&!this.logged) value.qq_login={available:true,phase:'waiting_qrcode',online:false};
    return JSON.stringify(value,null,2)+(this.running&&!this.logged?'\n巡检提示：qq_scan_required。需要本人手机确认登录。':'')+'\n[教学简化输出，不是实时服务器数据]';
  }
  run(raw) {
    const command=raw.trim().replace(/\s+/g,' '); if(!command) return '';
    this.history.push(command);
    if(command==='help') return '可用：pwd、ls、cd 路径、ssh -F .deployment/ssh_config qqbot-cloud、exit、clear\n在 Mac 项目目录：bash scripts/cloud/remote.sh status|webui|configure-login|start|stop|backup\n本模拟器只识别教学命令，不会连接任何服务器。';
    if(this.tunnel && this.tunnelTerminal===this.active) return '这个终端正在维持隧道，暂时不能执行新命令。点击“打开第二个终端”，或 Control + C 结束隧道。';
    if(command==='clear') return null;
    const t=this.terminal;
    if(command==='pwd') { this.emit(t.remote?'remote-pwd':'pwd'); return t.cwd; }
    if(command==='ls' || command==='ls -la') { this.emit('ls'); return t.remote?'qqbot  snapshots':t.cwd===Academy.project?'README.md  scripts/  docs/  compose.cloud.yaml  learning-site/':'Desktop/  Documents/  Downloads/'; }
    if(command==='cd' || command.startsWith('cd ')) {
      let dest=command==='cd'?'~':command.slice(3).replace(/^["']|["']$/g,'');
      if(dest==='~') dest=t.remote?'/home/learner':'/Users/firefly';
      if(dest.startsWith('~/')) dest=(t.remote?'/home/learner':'/Users/firefly')+dest.slice(1);
      if(dest==='..') dest=t.cwd.split('/').slice(0,-1).join('/')||'/';
      if(!dest.startsWith('/')) dest=t.cwd.replace(/\/$/,'')+'/'+dest;
      const known=t.remote?['/','/home','/home/learner','/opt','/opt/qqbot']:['/','/Users','/Users/firefly','/Users/firefly/Documents','/Users/firefly/Documents/Projects',Academy.project];
      if(!known.includes(dest)) return 'cd: 没有这个教学目录。试试 cd '+(t.remote?'/opt/qqbot':Academy.project);
      t.cwd=dest; if(dest===Academy.project) this.emit('cd'); return '';
    }
    if(command==='exit') { if(!t.remote) return '当前已在 Mac。本模拟窗口保留；真实终端中 exit 会结束 shell。'; t.remote=false; t.cwd=t.localCwd; this.emit('exit'); return 'SSH 连接已结束，回到 Mac。云端程序仍可继续运行。'; }
    if(command==='ssh -F .deployment/ssh_config qqbot-cloud') {
      if(t.remote) return '你已经在服务器。输入 exit 回到 Mac。';
      if(t.cwd!==Academy.project) return '无法读取相对路径 .deployment/ssh_config：先在 Mac 进入 QQbot 项目目录。';
      if(!this.sshAvailable) return 'ssh: connect to host qqbot-cloud: Connection timed out\n模拟网络故障：先检查网络和云实例，尚未登录服务器。';
      t.localCwd=t.cwd; t.cwd='/home/learner'; t.remote=true; this.emit('ssh'); return '【模拟 SSH】已进入 Ubuntu 服务器。注意提示符和目录的变化。';
    }
    const match=command.match(/^bash (?:\.\/)?scripts\/cloud\/remote\.sh(?: (\S+))?$/);
    if(match) {
      if(t.remote) return 'remote.sh 是 Mac 上的入口。先输入 exit 回到 Mac 项目目录。';
      if(t.cwd!==Academy.project) return 'bash: scripts/cloud/remote.sh: No such file or directory\n先执行 cd '+Academy.project;
      if(!this.sshAvailable) return 'ssh: connect to host qqbot-cloud: Connection timed out\n请求没有到达服务器，先检查连接。';
      const action=match[1]||'status';
      if(action==='status') return this.status();
      if(action==='webui') {
        if(this.tunnel||this.busy) return 'bind: Address already in use\n本机 16099 端口已占用。检查之前是否已开隧道窗口，勿盲目结束未知进程。';
        this.tunnel=true; this.tunnelTerminal=this.active; this.emit('webui');
        return 'Open http://127.0.0.1:16099/webui/ while this terminal stays open.\n【模拟】隧道正在运行。保留此终端，用下方按钮模拟手机确认。';
      }
      if(action==='configure-login') {
        if(!this.ready) return '无法设置：QQ 尚未成功登录。先完成本人手机确认。';
        this.emit('configure-login'); return '【模拟】已将当前验证成功的 QQ 账号设为快捷登录账号。QQ 仍可能要求再次手机验证。';
      }
      if(action==='start') { this.running=true; this.emit('start'); return '【模拟】已请求启动服务。请再次运行 status；如果需要扫码，继续完成 QQ 登录。'; }
      if(action==='stop') { this.running=false; this.emit('stop'); return '【模拟】服务已停止，已设置维护标记。真实执行会让机器人停止工作。'; }
      if(action==='backup') { this.emit('backup'); return '【模拟】备份创建成功：/opt/qqbot/backups/example.tar.gz\n这里只演示结果，没有创建真实文件。真实备份含私密配置，需要妥善保管。'; }
      return '不支持这个参数。输入 help 查看教学命令。';
    }
    return '模拟器没有执行这条命令。输入 help 查看支持的命令；请检查空格和英文拼写。不要在这里输入密码或 Token。';
  }
};
