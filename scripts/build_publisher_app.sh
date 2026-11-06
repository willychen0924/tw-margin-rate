#!/bin/zsh
set -eu
TASK_ROOT="${0:A:h:h}"
TASK_BUILD="$(mktemp -d)"
mkdir -p "$TASK_ROOT/更新並發布.app/Contents/MacOS"
for TASK_ARCH in arm64 x86_64; do
  /usr/bin/swiftc -O -target "$TASK_ARCH-apple-macos14.0" -module-cache-path "$TASK_BUILD/module-cache" "$TASK_ROOT/scripts/PublisherApp.swift" -o "$TASK_BUILD/$TASK_ARCH"
done
/usr/bin/lipo -create "$TASK_BUILD/arm64" "$TASK_BUILD/x86_64" -output "$TASK_ROOT/更新並發布.app/Contents/MacOS/MarginPublisher"
# iCloud may attach Finder's cosmetic metadata to the new bundle; it is not code.
/usr/bin/xattr -d com.apple.FinderInfo "$TASK_ROOT/更新並發布.app" 2>/dev/null || true
/usr/bin/codesign --force --sign - "$TASK_ROOT/更新並發布.app"
print "App 已建立（Apple Silicon／Intel）。暫存編譯資料：$TASK_BUILD"
