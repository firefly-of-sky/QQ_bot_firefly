"""Private, durable operator receipts; uncertain writes are never replayed."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class OperatorStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operator_settings(id INTEGER PRIMARY KEY,paused INTEGER NOT NULL);
                INSERT OR IGNORE INTO operator_settings VALUES(1,0);
                CREATE TABLE IF NOT EXISTS operator_calls(
                  key TEXT PRIMARY KEY,turn_id TEXT NOT NULL,tool TEXT NOT NULL,action TEXT NOT NULL,
                  state TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS operator_calls_turn ON operator_calls(turn_id,tool,action);
                CREATE TABLE IF NOT EXISTS operator_jobs(
                  id TEXT PRIMARY KEY,subject TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
            ''')
            # A new owning process cannot know whether an interrupted dispatch
            # reached QQ. Preserve the receipt instead of starting a fresh write.
            db.execute("UPDATE operator_calls SET state='UNKNOWN',result=?,updated=? WHERE state='EXECUTING'",
                       (canonical({'state':'UNKNOWN','note':'进程中断，结果未知，禁止自动重发。'}),time.time()))
            db.execute("UPDATE operator_jobs SET state='UNKNOWN',updated=? WHERE state='RUNNING'",(time.time(),))
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @property
    def paused(self):
        with self.connect() as db:return bool(db.execute('SELECT paused FROM operator_settings WHERE id=1').fetchone()[0])

    def pause(self,value):
        with self.connect() as db:db.execute('UPDATE operator_settings SET paused=? WHERE id=1',(int(value),))

    def begin(self,turn,tool,action,args,limit):
        key='qqbot-'+hashlib.sha256(canonical([turn,tool,action,args]).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT result,state FROM operator_calls WHERE key=?',(key,)).fetchone()
            if row:return key,json.loads(row['result'])|{'state':row['state'],'cached':True}
            row=db.execute("SELECT result FROM operator_calls WHERE turn_id=? AND tool=? AND action=? AND state IN ('EXECUTING','UNKNOWN') LIMIT 1",(turn,tool,action)).fetchone()
            if row:return key,{'state':'UNKNOWN','note':'同一输入已有结果未知的同类操作，不更换参数或编号重发。'}
            count=db.execute('SELECT count(*) FROM operator_calls WHERE created>?',(time.time()-60,)).fetchone()[0]
            if count>=limit:return key,{'state':'RATE_LIMITED','note':'QQ 操作达到本分钟限额。'}
            now=time.time()
            db.execute('INSERT INTO operator_calls VALUES(?,?,?,?,?,?,?,?)',
                (key,turn,tool,action,'EXECUTING',canonical({'state':'UNKNOWN'}),now,now))
        return key,None

    def finish(self,key,result,state=None):
        state=state or result.get('state','UNKNOWN')
        with self.connect() as db:
            db.execute('UPDATE operator_calls SET state=?,result=?,updated=? WHERE key=?',
                       (state,canonical(result),time.time(),key))

    def enqueue(self,event_id,subject):
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM operator_jobs WHERE state='PENDING'").fetchone()[0]>=32:return False
            now=time.time()
            return bool(db.execute('INSERT OR IGNORE INTO operator_jobs VALUES(?,?,?,?,?)',
                        (event_id,subject,'PENDING',now,now)).rowcount)

    def next_job(self,limit):
        with self.connect() as db:
            if self.paused:return None
            db.execute('BEGIN IMMEDIATE')
            count=db.execute("SELECT count(*) FROM operator_jobs WHERE state!='PENDING' AND updated>?",(time.time()-3600,)).fetchone()[0]
            if count>=limit:return None
            row=db.execute("SELECT * FROM operator_jobs WHERE state='PENDING' ORDER BY created LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE operator_jobs SET state='RUNNING',updated=? WHERE id=?",(time.time(),row['id']))
            return dict(row)

    def finish_job(self,job,state):
        with self.connect() as db:db.execute('UPDATE operator_jobs SET state=?,updated=? WHERE id=?',(state,time.time(),job))

    def summary(self):
        with self.connect() as db:
            return dict(paused=self.paused,calls={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM operator_calls GROUP BY state')},
                        jobs={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM operator_jobs GROUP BY state')})
