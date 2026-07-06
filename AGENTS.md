# AGENTS.md

**このリポジトリの共通ルールはすべて [CLAUDE.md](CLAUDE.md) に一本化している。作業前に必ず全文を読むこと。**

（このファイルは Claude Code 以外のエージェント向けの入口。内容の二重管理を避けるため、ここにはルールを書かない）

- 共通ルール・壊してはいけない仕様・検証コマンド → `CLAUDE.md`
- 実装手順（調査→方針→最小差分→段階検証） → `.claude/skills/safe-implement/SKILL.md`
- 差分レビュー手順 → `.claude/skills/fable-review/SKILL.md`
- モデル別の助言・検証レシピ集 → `docs/agent-handoff-notes.md`

補足（CLAUDE.md未記載の作業対象ルール）:
- 主対象は `run-story` 系（story_editor / StoryVideo）。`legacy-dialogue/` は凍結中で、明示的な再開指示がない限り触らない
