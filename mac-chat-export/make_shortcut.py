#!/usr/bin/env python3
"""Build a native macOS Shortcut that opens the companion app."""
from pathlib import Path
import plistlib
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent


def main():
    target = ROOT / "QQ聊天整理.shortcut"
    unsigned = ROOT / "build" / "QQ聊天整理-unsigned.wflow"
    shortcut = {
        "WFWorkflowName": "QQ聊天整理（Mac）",
        "WFWorkflowClientVersion": "4018.0.4",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983,
                           "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowTypes": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowImportQuestions": [],
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFQuickActionSurfaces": [],
        "WFWorkflowActions": [{
            "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {
                "WFInput": "qqchatarchive://open",
                "UUID": str(uuid.uuid4()).upper(),
            },
        }],
    }
    unsigned.parent.mkdir(parents=True, exist_ok=True)
    unsigned.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_XML))
    subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                    "--output", str(target)], check=True)
    print("Mac 快捷指令已生成：" + str(target))


if __name__ == "__main__":
    main()
