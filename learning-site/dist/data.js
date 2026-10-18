/* Course content is deliberately local: no credentials, analytics, or server calls. */
globalThis.Academy = {};
Academy.project = '/Users/firefly/Documents/Projects/QQbot';
Academy.remote = action => `bash scripts/cloud/remote.sh ${action}`;
Academy.tokenCommand = `ssh -F .deployment/ssh_config qqbot-cloud 'python3 -' <<'PY'
import json
from pathlib import Path
config = json.loads(Path('/opt/qqbot/data/napcat/webui.json').read_text())
token = config.get('token')
if not isinstance(token, str) or not token:
    raise SystemExit('WebUI token not found; stop and check the server configuration.')
print(token)
PY`;
Academy.lessons = [
  {
    title:'先认识你的服务器', subtitle:'把看不见的机器，变成一张清楚的地图', icon:'◎', tag:'认识环境',
    intro:'服务器就是一台放在机房、可以一直开着的电脑。你用 Mac 管理它；QQbot 在那台电脑上运行。先弄清“谁在哪里”，后面的命令就有了方向。',
    blocks:[
      ['你的 Mac 是遥控器','这是你眼前的电脑，运行 macOS。你在这里打开终端、输入管理命令，也用这里的浏览器查看管理页面。Mac 关机不会直接关掉云端机器人。'],
      ['云服务器是工作现场','项目记录中的服务器使用 Ubuntu 22.04。Ubuntu 是操作系统，和 macOS 一样负责管理文件、网络、程序。云服务器有 CPU、内存和磁盘，模型调用通过远程 API 完成。'],
      ['Docker 把程序装进各自的盒子','容器可以理解为程序的运行盒子。这里主要有 napcat 和 bot 两个服务：NapCat 负责 QQ 登录与连接；QQbot 负责处理消息并调用模型。Compose 用一份配置一起管理它们。'],
      ['别把“在线”混成一件事','容器运行，只能说明程序在跑；QQ 登录成功，说明账号接入；ready=true 表示 QQ、接收器和会话进程已就绪。最后还要从另一个 QQ 发消息，才能确认回复链路。']
    ],
    takeaway:'Mac 负责管理，服务器负责运行；NapCat 登录 QQ，QQbot 处理回复。',
    tasks:[['pwd','输入 pwd，看看模拟终端现在在哪里'],['status','进入项目目录，然后查看云端状态']],
    commands:['pwd','cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh status'],
    real:[['打开终端','按 Command + 空格，输入“终端”或 Terminal，再按回车。看到 % 或 $ 提示符就能输入命令。'],['找到项目','在 Mac 终端输入下方的 cd 命令，再输入 pwd。输出应当是 QQbot 项目目录。不要把提示符 % 或 $ 一起复制。'],['辨认三层角色','用自己的话说出 Mac、Ubuntu 服务器、NapCat 各负责什么。今天还不需要改动任何服务。']],
    quiz:[['Mac 关机后，云端 QQbot 会怎样？',['必然一起关闭','通常继续运行，管理连接会断开','QQ 账号自动退出'],1,'机器人在云端运行。关闭 Mac 或隧道只会影响你管理它。'],['哪个程序负责 QQ 登录？',['NapCat','浏览器','远程模型'],0,'NapCat 维护 QQ 登录与连接，QQbot 处理消息。']]
  },
  {
    title:'第一次和终端说话', subtitle:'学会找位置、进目录、读懂一条命令', icon:'>_', tag:'终端基础',
    intro:'终端是用文字给电脑下指令的窗口。你输入一行，按回车，电脑执行并返回文字。先掌握三个词：当前位置、目录、命令。',
    blocks:[
      ['文件夹也叫目录','/ 是路径分隔符。/Users/firefly 是 Mac 上的一个目录；/opt/qqbot 是服务器上的项目目录。相同的一条路径，在不同电脑上不一定存在。绝对路径以 / 开头，相对路径从当前位置找起。'],
      ['三个先用熟的命令','pwd 显示当前目录；ls 列出目录中的文件；cd 路径 切换目录。cd .. 回到上一层；cd ~ 回到当前用户的主目录。cd 成功时常常没有任何输出，这不是失败。'],
      ['拆开你的管理命令','bash scripts/cloud/remote.sh status：bash 是执行脚本的程序；scripts/cloud/remote.sh 是相对路径；status 是告诉脚本“查看状态”的参数。所以必须先进入 QQbot 项目目录。'],
      ['键盘也能帮你','在真实终端用 ↑ 找上一条命令，Tab 补全路径，Control + C 中断当前前台任务。Command + C 通常是复制，不是中断。输入密码时可能不显示字符；不要因此反复粘贴。']
    ],
    takeaway:'每次执行项目脚本前，先确认“我在哪台电脑、哪个目录”。',
    tasks:[['pwd','查看当前位置'],['cd','切换到 QQbot 项目目录'],['ls','用 ls 找到 scripts 目录'],['status','执行项目的状态命令']],
    commands:['pwd','cd /Users/firefly/Documents/Projects/QQbot','ls','bash scripts/cloud/remote.sh status'],
    real:[['先进入目录','在 Mac 终端执行 cd /Users/firefly/Documents/Projects/QQbot。后续课程出现的 remote.sh 命令，都在这个目录执行。'],['检查文件','执行 ls，确认能看到 scripts。执行 pwd，再确认是 Mac 上的 QQbot 目录。'],['读懂报错','No such file or directory 常常是路径错误。先检查 pwd 和拼写，不要尝试删除文件或重装。']],
    quiz:[['cd 成功后没有输出，应如何理解？',['一定失败了','通常正常，可以用 pwd 确认','必须再执行十次'],1,'很多命令成功时很安静。用 pwd 验证即可。'],['提示找不到 scripts/cloud/remote.sh，先做什么？',['删除 scripts','用 pwd 检查目录，再进入项目','重装系统'],1,'脚本路径是相对路径，先检查你在哪里。']]
  },
  {
    title:'连上远方的电脑', subtitle:'理解 SSH，以及“命令在哪执行”', icon:'↗', tag:'远程连接',
    intro:'SSH 是一条加密的远程管理通道。你在 Mac 发出命令，服务器执行并把结果送回来。项目已经配好专用 SSH；先使用现有入口，无需重新购买或配置服务器。',
    blocks:[
      ['连接需要三样东西','服务器地址告诉你去哪里，用户名说明以谁的身份进入，密钥证明你有权限。项目的 .deployment/ssh_config 保存连接设置，专用私钥也在本机私有目录。课程不会收集它们。'],
      ['一次执行，还是留在远程？','remote.sh status 会在服务器执行一次状态检查，然后回到 Mac 提示符。普通 ssh 登录会让你停留在远程 shell，直到输入 exit。看提示符和 pwd，可以帮助判断当前位置。'],
      ['先用封装好的入口','日常管理用 bash scripts/cloud/remote.sh status。它替你带上专用配置并执行服务器上的 ops.py。理解封装的意义后，你就知道这不是“在 Mac 本地运行机器人”。'],
      ['连接问题有不同原因','超时可能是网络、实例或安全组问题；Permission denied (publickey) 说明身份验证未通过；主机指纹变化需要核对云平台和服务器身份，不能直接忽略校验。换电脑需要重新授权管理公钥。']
    ],
    takeaway:'命令从 Mac 输入，不等于命令在 Mac 执行；SSH 把管理请求送到云端。',
    tasks:[['status','在 Mac 项目目录运行远程状态检查'],['ssh','在模拟环境中用 SSH 进入服务器'],['remote-pwd','在服务器用 pwd 判断位置'],['exit','输入 exit 回到 Mac']],
    commands:['cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh status','ssh -F .deployment/ssh_config qqbot-cloud','pwd','exit'],
    real:[['先检查连接','在 Mac 的项目目录执行 remote.sh status。能返回云端状态，说明这次 SSH 管理请求成功。'],['观察回到哪里','命令结束后再次输入 pwd，应该仍然是 Mac 项目目录。日常操作用 remote.sh 就够了，模拟课里的交互 SSH 不必在真机重复。'],['连接失败时','如果提示 Project SSH configuration is missing，检查是否是原来这台 Mac、是否在正确项目。不要把私钥或 .env 粘贴到网站。']],
    quiz:[['remote.sh status 执行后，终端通常在哪？',['停留在服务器 shell','回到 Mac 的原目录','进入手机 QQ'],1,'脚本执行远程命令后退出 SSH；你回到 Mac。'],['Permission denied (publickey) 主要提示什么？',['QQ 密码错了','SSH 身份验证未通过','磁盘已满'],1,'这是 SSH 验证问题，先检查既有配置与密钥授权。']]
  },
  {
    title:'看懂机器人是否在线', subtitle:'从状态输出中找到下一步', icon:'◉', tag:'状态判断',
    intro:'运维的第一步是观察。先运行 status，再根据结果行动。不要看到不回复，就连续重启。这里练习的是缩短后的教学输出，真实输出字段可能更多。',
    blocks:[
      ['running：程序正在运行','services 里的 bot、napcat 状态能告诉你两个服务是否在跑。容器 running 不等于 QQ 已经登录。你要继续看 application 里的 live、ready 和原因。'],
      ['live 与 ready 分别说明什么','live=true 表示程序能响应健康检查。ready=true 表示 QQ、框架接收器与当前会话工作进程就绪。两者都为 true，仍要通过真实 QQ 消息验证模型和回复。'],
      ['等待扫码不需要盲目重启','登录阶段 waiting_qrcode 或巡检提示 qq_scan_required，表示需要手机确认 QQ 登录。保持现有程序运行，使用 WebUI 扫码。二维码过期就刷新页面获取新的二维码。'],
      ['QQ 在线但不回复','先用另一个 QQ 给机器人发一条简单消息，并等待。检查好友/群权限、网络以及模型服务是否可用。ready 不是余额或模型 API 的检测结果；记录报错类别，不公开原始聊天或密钥。']
    ],
    takeaway:'先判断是连接问题、程序问题、登录问题，还是回复链路问题。',
    scenario:'scan',
    tasks:[['status','查看当前“等待扫码”场景的状态']],
    commands:['cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh status'],
    real:[['读一次状态','在 Mac 项目目录执行 status。先找两个服务状态，再找 application 中的 live / ready；不要把本页示例当作服务器实时状态。'],['判断下一步','需要扫码就进入下一课；服务停止再看恢复课；SSH 报错则先检查连接。'],['验证回复','如果 ready=true，从另一个 QQ 向机器人发送简单消息。QQ 是否在线和模型是否能回答，要分别验证。']],
    quiz:[['容器 running，但 QQ 等待扫码，下一步？',['反复重启服务器','打开 WebUI，用手机确认登录','删除 QQ 数据'],1,'这是登录问题，重启不能代替本人扫码。'],['ready=true 能保证模型正常回复吗？',['能','不能，还要发消息验证','说明没有安装模型'],1,'ready 只覆盖连接和工作进程，模型服务还需真实消息验证。']]
  },
  {
    title:'亲手完成 QQ 登录', subtitle:'建立隧道、手机确认、检查回复', icon:'▦', tag:'核心实操',
    intro:'这节课把整个登录流程连起来。你会开着一个终端维持管理通道，再用另一个终端检查状态。模拟环境里的“扫码”只是练习按钮，没有真实二维码。',
    blocks:[
      ['隧道是一条临时的管理通道','remote.sh webui 把 Mac 的 127.0.0.1:16099 转发到服务器的 127.0.0.1:6099。127.0.0.1 代表“当前这台电脑自己”，所以这两个地址属于不同电脑。端口可以理解为程序的门牌号。'],
      ['第一个终端要保持开启','在 Mac 项目目录运行 webui，终端一直不返回提示符是正常的：它正在维持 SSH 隧道。保留这个窗口，用 Mac 浏览器打开 http://127.0.0.1:16099/webui/。'],
      ['管理页登录与 QQ 登录是两步','如果 WebUI 要求 Token，这是管理页凭证，不是 QQ 密码。从你自己的既有安全记录中取用；找不到时，可按本课“真实操作”中的说明，在自己的 Mac 终端查看。进入 QQ 登录页面后，用机器人账号的手机 QQ 扫码并确认；可能还需手机验证。'],
      ['成功后设置续登并验收','在 Mac 终端按 Command + N 新开一个窗口，重新进入项目目录。运行 configure-login 设置当前已验证账号的快捷登录，再运行 status。最后从另一个 QQ 发消息验证回复。快捷登录不能保证永久免扫码。'],
      ['完成后关闭通道','回到第一个终端，按 Control + C 结束隧道。管理页会断开，云端机器人通常继续运行。不要把 6099、3001 等管理端口开放到公网。']
    ],
    takeaway:'开隧道 → 浏览器管理页 → 手机确认 → 第二终端设续登 → 查状态 → 真实消息验证。',
    scenario:'scan',
    tasks:[['webui','在 Mac 项目目录建立管理隧道'],['scan','点击练习中的“模拟手机扫码并确认”'],['configure-login','打开第二个模拟终端，设置快捷登录'],['ready-status','确认 ready=true'],['message','点击“模拟发消息验证”'],['tunnel-closed','回到隧道终端，用 Control + C 关闭通道']],
    commands:['cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh webui','bash scripts/cloud/remote.sh configure-login','bash scripts/cloud/remote.sh status'],
    real:[['Mac 终端 A：保持隧道','先进入 QQbot 项目，再运行 bash scripts/cloud/remote.sh webui。看到打开页面的提示后，不要关闭窗口。'],['Mac 浏览器：打开管理页','手动在 Mac 浏览器地址栏输入 http://127.0.0.1:16099/webui/。若提示 WebUI Token，使用自己的管理凭证登录；不要把 Token 填进学习网站。'],['手机：本人完成确认','用机器人账号的手机 QQ 扫描管理页中的真实二维码并确认。二维码过期就刷新；按 QQ 提示完成额外验证。'],['Mac 终端 B：设置续登','按 Command + N 新建终端窗口，再执行 cd 进入项目，运行 configure-login。此操作必须在 QQ 已成功登录后执行。'],['Mac 终端 B：验证状态和消息','运行 status，确认 ready=true；从另一个 QQ 发消息并收到回复。仅看到登录成功不算完成回复验收。'],['Mac 终端 A：结束隧道','回到仍在运行 webui 的窗口按 Control + C。再从终端 B 查状态，理解关闭管理通道不等于关闭机器人。']],
    quiz:[['webui 命令一直占着终端，没有提示符，说明什么？',['通常在维持隧道，应保留窗口','一定卡死，必须重启服务器','QQ 已经永久登录'],0,'SSH 隧道是持续运行的前台任务。另开窗口执行其他命令。'],['什么时候执行 configure-login？',['手机成功确认 QQ 登录后','还没扫码时','任何时候都能绕过验证'],0,'它绑定当前已验证账号，不能绕过手机确认。']]
  },
  {
    title:'出问题，也知道从哪开始', subtitle:'排查、恢复和备份，一次只改一件事', icon:'↺', tag:'日常运维',
    intro:'排查不是试遍所有命令，而是先判断哪一层出了问题。你已有状态、启动、停止、备份等入口。练习中可以放心试；真实 stop 会停止机器人服务。',
    blocks:[
      ['先观察，再恢复','status 是第一步。若服务已停止且你打算恢复运行，使用 start，再查 status；若只是 QQ 等待扫码，就走扫码流程。stop 会设置维护标记，让监控不要擅自拉起。'],
      ['网页打不开，先检查隧道','确认用的是原来那台 Mac、webui 终端还在运行、地址是本机 16099。Address already in use 表示本机端口被占用；先找自己之前的隧道窗口，别盲目结束不认识的进程。SSH 超时则先排查网络和云端实例。'],
      ['备份是另一个动作','backup 会创建备份。项目有每天约 04:20 的定时备份并保留 7 份，但同一云盘上的备份不能防云盘丢失。备份包含私密配置，应像密钥一样保管；QQ 设备登录目录不作在线复制。'],
      ['先不练重装和恢复覆盖','真实恢复会覆盖数据，需要确认版本、检查完整性、保留回退副本。第一阶段先会查看状态、手动备份和恢复运行。不要用删数据、删除登录目录、重装系统来解决普通掉线。']
    ],
    takeaway:'状态 → 定位原因 → 一次操作 → 再查状态 → 发消息验证。',
    scenario:'stopped',
    tasks:[['status','检查“服务停止”场景'],['start','恢复运行'],['ready-status','再次确认就绪'],['backup','创建一次模拟备份'],['message','模拟发消息，确认完整链路']],
    commands:['cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh status','bash scripts/cloud/remote.sh start','bash scripts/cloud/remote.sh backup'],
    real:[['只观察，不主动制造故障','先运行 status。如果服务正常，不必为了学习 stop 或重启。'],['按原因处理','服务确已停止才 start；等待扫码就扫码；QQ 已在线但不回复则继续检查消息权限和模型服务。操作后再次查状态。'],['手动备份','运行 backup 并阅读结果。备份含私密配置，不要上传或发送给别人。今天不执行覆盖式恢复。'],['记录验收','用另一个 QQ 发消息并验证。把错误类别与处理步骤记下，不记录密码、Token 或私聊内容。']],
    quiz:[['QQ 等待扫码时，哪种操作最合适？',['删除登录数据','打开管理页重新确认登录','不断 stop / start'],1,'本人确认登录是必要步骤，循环重启不能替代。'],['同一云盘的备份能防止云盘丢失吗？',['能，永远安全','不能，需要另做异地备份','只要文件名不同就能'],1,'本机备份和原数据共享同一块盘的风险。']]
  },
  {
    title:'最后一次，自己完成', subtitle:'收起答案，把整个流程走一遍', icon:'✳', tag:'独立挑战',
    intro:'这次先不展开命令答案。模拟环境从 QQ 掉线开始，你来判断并完成恢复。之后再在自己的 Mac 上做真实验收；模拟通关与真实完成会分别记录。',
    blocks:[
      ['你的任务','你收到“机器人不回复”的反馈。先查状态，确认是需要扫码；建立隧道并确认登录；设置快捷登录；再次看状态；模拟发消息验证；最后结束隧道。'],
      ['真正学会的标志','你能说明每条命令在哪里执行、为什么执行，也能在出错时停下来定位。忘记拼写可以查速查表，理解流程比背住全部字符更重要。'],
      ['真实验收由你勾选','网站不会连接服务器，也无法判断真实 QQ 状态。实操清单是你的个人确认：必须亲自观察并验证后勾选。模拟通过只证明练习完成。']
    ],
    takeaway:'能独立判断并走完流程，比让 AI 替你执行更有用。',
    scenario:'scan', challenge:true,
    tasks:[['status','独立查看并判断状态'],['webui','建立隧道'],['scan','完成模拟扫码'],['configure-login','在第二终端设置续登'],['ready-status','确认就绪'],['message','验证回复'],['tunnel-closed','结束隧道']],
    commands:['cd /Users/firefly/Documents/Projects/QQbot','bash scripts/cloud/remote.sh status','bash scripts/cloud/remote.sh webui','bash scripts/cloud/remote.sh configure-login'],
    real:[['准备就绪','在 Mac 打开终端并进入项目；能说出这个目录在 Mac 上。'],['独立读取状态','运行状态检查，区分容器运行、QQ 在线和模型回复。'],['打开管理页','自己建立隧道，在 Mac 浏览器打开本机 16099 管理页。'],['完成 QQ 登录判断','如果掉线，用手机 QQ 扫码并确认；若已在线，明确判断无需重新扫码。'],['检查快捷登录与就绪','若本次重新扫码，在第二终端执行 configure-login，再查 ready；若已在线，复核状态即可。'],['真实消息验收','从另一个 QQ 发消息，亲自收到机器人回复。'],['关闭管理通道','结束隧道后，机器人仍能正常回复。']],
    quiz:[['手机确认成功后，怎样验证完整恢复？',['只看容器 running','查 ready，再用另一个 QQ 验证回复','关闭网页就算完成'],1,'状态与真实消息共同完成验收。'],['终端显示主机指纹发生变化，应该？',['禁用校验继续连接','停下并核对云平台与服务器身份','发给陌生人私钥求助'],1,'先核对服务器身份，不能为了连接成功跳过验证。']]
  }
];
Academy.glossary = [
  ['服务器','放在机房、可长期运行的电脑。你的 QQbot 在这里运行。'],['Ubuntu','服务器使用的操作系统，负责管理程序、文件和网络。'],['终端','用文字输入命令、查看执行结果的窗口。'],['Shell','读取和执行命令的程序。终端是窗口，shell 在里面工作。'],['目录 / 路径','目录就是文件夹；路径说明去哪个位置找文件。'],['SSH','加密的远程管理连接。你在 Mac 发出请求，云端执行。'],['密钥 / 公钥','私钥保存在自己的设备，公钥可授权到服务器。不要分享私钥。'],['IP / 端口','IP 类似地址，端口类似这个地址上的门牌号。'],['127.0.0.1','当前这台电脑自己的回环地址。必须分清是 Mac 还是服务器。'],['隧道','把本机端口通过 SSH 转发到服务器端口的管理通道。'],['Docker / 容器','为程序提供运行环境；容器运行不表示 QQ 一定在线。'],['Compose','用配置文件统一管理多个容器服务的工具。'],['NapCat','负责登录 QQ 并提供机器人框架使用的连接。'],['OneBot','NapCat 与机器人程序之间使用的接口约定。'],['WebUI / Token','浏览器管理页及其访问凭证。它与 QQ 登录是两回事。'],['live / ready','live 看进程响应；ready 看 QQ、接收器和会话进程就绪。'],['API / 模型','机器人通过远程 API 调用模型；账号余额和接口故障可能影响回复。'],['备份','保存可用于恢复的数据副本；含私密配置，需要妥善保管。']
];
Academy.troubles = [
  ['找不到 remote.sh','No such file or directory','先在 Mac 输入 pwd，确认当前目录。再执行 cd /Users/firefly/Documents/Projects/QQbot，用 ls 检查 scripts 目录。','status'],
  ['提示缺少 SSH 配置','Project SSH configuration is missing','确认在原来已配置专用 SSH 的 Mac 上。脚本会自行进入项目目录，仍缺失说明 .deployment/ssh_config 不存在。不要新建空文件冒充配置；新设备需要授权管理公钥。',null],
  ['SSH 连接超时','Connection timed out','先确认 Mac 能上网，再在云平台查看实例运行状态、账单与已有安全组设置。区分端口被阻断和实例停止；不要为访问管理页开放 6099 或 3001。',null],
  ['SSH 拒绝密钥','Permission denied (publickey)','核对是否使用专用 SSH 配置和原来授权的设备。它不是 QQ 密码错误。不要把私钥复制给别人，也不要绕过主机校验。',null],
  ['本机管理页打不开','127.0.0.1:16099 无法访问','在 Mac 项目目录运行 webui，保持该终端开启；在同一台 Mac 的浏览器手动打开 http://127.0.0.1:16099/webui/。网站上的模拟隧道不会建立真实连接。','webui'],
  ['端口已经占用','Address already in use','先检查自己是否已经开过一个 webui 隧道窗口。使用现有隧道，或在确认属于自己的旧窗口中 Control + C 后重建。不要盲目杀死未知进程。',null],
  ['管理页要求 Token','WebUI 登录凭证','Token 用于登录管理页，不是 QQ 密码。从自己的安全记录中取用既有凭证。若未保存，展开下方指引，在自己的 Mac 终端通过既有 SSH 只读查看。不要把凭证填入本学习网站。',null],
  ['QQ 需要重新扫码','qq_scan_required / waiting_qrcode','开隧道 → 在 Mac 浏览器进 WebUI → 用机器人账号手机 QQ 扫码并确认 → 第二终端设置快捷登录 → 查状态并发消息验证。','webui'],
  ['QQ 在线但不回复','ready=true，但没有收到回答','从另一个 QQ 发一条简单消息。检查好友或群权限、模型 API、模型账户余额和网络。ready 不能证明模型服务可用。避免公开原始日志、Token 或聊天记录。',null],
  ['服务确实停止','services 中 bot 或 napcat 未运行','若你希望恢复服务，在 Mac 项目目录运行 start，再查 status。若随后提示等待扫码，继续登录流程。不要用重启替代原因判断。','start']
];
