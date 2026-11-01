"""Add read-only kick diagnostics to one verified NapCat build; never suppress logout."""
import argparse
import hashlib
from pathlib import Path

SOURCE_SHA256 = '96934cbd3a4a7ad4d50bfeeaa10adb600fd142cbb4f1be00ce427287fd5926fb'
HEADER = 'async initNapCatCoreListeners() {\n    const e = new EM();'
TIMED_HEADER = HEADER.replace('    const e', '    const __qqbotSessionStartedAt = Date.now();\n    const e')
CALLBACK = 'this.context.logger.logError(i), this.selfInfo.online = !1, this.event.emit("KickedOffLine", i);'
DIAGNOSTIC = '''this.context.logger.logWarn("[QQbotDiag/v1] " + JSON.stringify({
        event: "qq_kicked_offline", at: new Date().toISOString(),
        sessionUptimeSeconds: Math.max(0, Math.floor((Date.now() - __qqbotSessionStartedAt) / 1000)),
        workerUptimeSeconds: Math.floor(process.uptime()),
        appId: Number.isSafeInteger(r.appId) ? r.appId : null,
        instanceId: Number.isSafeInteger(r.instanceId) ? r.instanceId : null,
        sameDevice: typeof r.sameDevice === "boolean" ? r.sameDevice : null,
        kickedType: Number.isSafeInteger(r.kickedType) ? r.kickedType : null,
        securityKickedType: Number.isSafeInteger(r.securityKickedType) ? r.securityKickedType : null
      }));
      ''' + CALLBACK


def patch_bundle(text, expected_sha256=SOURCE_SHA256):
    if '[QQbotDiag/v1]' in text:
        original=text.replace(TIMED_HEADER,HEADER).replace(DIAGNOSTIC,CALLBACK)
        if hashlib.sha256(original.encode()).hexdigest()!=expected_sha256:
            raise ValueError('Unexpected already-patched NapCat program')
        return text
    if hashlib.sha256(text.encode()).hexdigest()!=expected_sha256:
        raise ValueError('NapCat build differs from the reviewed source')
    if text.count(HEADER)!=1 or text.count(CALLBACK)!=1:
        raise ValueError('Expected exactly one native kick callback')
    return text.replace(HEADER,TIMED_HEADER).replace(CALLBACK,DIAGNOSTIC)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=patch_bundle(Path(args.source).read_text())
    Path(args.output).write_text(result)
    print('diagnostic_bundle_sha256='+hashlib.sha256(result.encode()).hexdigest())
