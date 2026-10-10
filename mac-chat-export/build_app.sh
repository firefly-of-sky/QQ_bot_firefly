#!/bin/bash
set -eu
TASK_ROOT="$(cd "$(dirname "$0")" && pwd)"
TASK_APP="$TASK_ROOT/build/QQ聊天整理.app"
mkdir -p "$TASK_APP/Contents/MacOS" "$TASK_ROOT/runtime/swift-cache"
swiftc -module-cache-path "$TASK_ROOT/runtime/swift-cache" \
    "$TASK_ROOT/QQ聊天整理.swift" -o "$TASK_APP/Contents/MacOS/QQChatArchive" -framework AppKit
cat > "$TASK_APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>QQChatArchive</string>
<key>CFBundleIdentifier</key><string>local.qqbot.chatarchive</string>
<key>CFBundleName</key><string>QQ聊天整理</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.3.0</string>
<key>CFBundleURLTypes</key><array><dict>
<key>CFBundleURLName</key><string>local.qqbot.chatarchive</string>
<key>CFBundleURLSchemes</key><array><string>qqchatarchive</string></array>
</dict></array>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
chmod +x "$TASK_ROOT/QQ聊天整理.command"
codesign --force --sign - "$TASK_APP"
echo "应用已生成：$TASK_APP"
