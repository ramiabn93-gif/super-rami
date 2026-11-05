#!/bin/bash
# Super Rami installer for Mac: double-click this file.
cd "$(dirname "$0")"
echo "🦸 تثبيت سوبر رامي..."
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
  echo "❌ تحتاج Python 3.9 أو أحدث. بفتح لك صفحة التحميل، ثبته وبعدها شغّل هذا الملف مرة ثانية."
  open "https://www.python.org/downloads/"
  read -r -p "اضغط Enter للإغلاق..."
  exit 1
fi
mkdir -p "$HOME/.claude/skills" "$HOME/super-rami-data"
rm -rf "$HOME/.claude/skills/super-rami"
cp -R skills/super-rami "$HOME/.claude/skills/"
chmod +x start-mac.command 2>/dev/null
echo "✅ تم التثبيت."
echo "   • في Claude Code قل: سوبر رامي، اختر لي أسهم"
echo "   • أو اضغط مرتين على start-mac.command لفتح القائمة"
if ! python3 -c "import ib_async" 2>/dev/null; then
  echo "ℹ️ بوت SPX يحتاج مكتبة إضافية. تثبتها الحين؟ (y/n)"
  read -r a
  [ "$a" = "y" ] && python3 -m pip install --user ib_async
fi
read -r -p "اضغط Enter للإغلاق..."
