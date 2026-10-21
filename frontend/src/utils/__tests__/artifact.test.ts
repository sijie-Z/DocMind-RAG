/**
 * 工件识别与下载工具（体验第四波）：
 * - 识别四类（diff/json/markdown/text），误判即回归——宁退 text 不误染色；
 * - diff 行解析的行号维护（hunk 重置、ctx 双增、add/del 单增）；
 * - 下载文件名与 Blob 落盘路径。
 *
 * 变异点：detectArtifactKind 去掉 diff 分支 → diff 用例红；parseDiffLines 的
 * hunk 行号重置改错 → 行号用例红；artifactFilename 不 sanitize → 文件名用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  artifactFilename,
  detectArtifactKind,
  downloadTextFile,
  isUnifiedDiff,
  looksLikeJson,
  looksLikeMarkdown,
  parseDiffLines,
} from '@/utils/artifact'

const GIT_DIFF = `diff --git a/src/app.py b/src/app.py
index 3a1f2c3..9b4d5e6 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,7 +10,8 @@ def main():
     setup()
-    run_old()
+    run_new()
+    log()
     teardown()
`

describe('工件识别 detectArtifactKind', () => {
  it('git diff（文件头 + hunk + 增删行）→ diff', () => {
    expect(detectArtifactKind(GIT_DIFF)).toBe('diff')
    expect(isUnifiedDiff(GIT_DIFF)).toBe(true)
  })

  it('无文件头但有 hunk + 增删行 → diff（patch 片段）', () => {
    const patch = '@@ -1,3 +1,4 @@\n a\n-b\n+c\n+d\n'
    expect(detectArtifactKind(patch)).toBe('diff')
  })

  it('被截断的文件式 diff（有 ---/+++ 头、无 hunk）→ diff', () => {
    const truncated = '--- a/x\n+++ b/x\n-old line\n+new line 1\n+new line 2\n'
    expect(detectArtifactKind(truncated)).toBe('diff')
  })

  it('负例：分隔线加单个减号开头的散文 → 非 diff', () => {
    const prose = '注意事项：\n---\n- 第一条\n这里是正文补充。\n'
    expect(detectArtifactKind(prose)).not.toBe('diff')
  })

  it('对象 / 数组 JSON → json；标量字符串 → 非 json', () => {
    expect(detectArtifactKind('{"a": 1, "b": [2, 3]}')).toBe('json')
    expect(detectArtifactKind('[1, 2, 3]')).toBe('json')
    expect(looksLikeJson('"just a string"')).toBe(false)
    expect(looksLikeJson('{not valid')).toBe(false)
  })

  it('markdown（标题 + 列表 ≥2 特征）→ markdown；纯句子 → text', () => {
    expect(detectArtifactKind('# 标题\n\n- 一\n- 二\n')).toBe('markdown')
    expect(detectArtifactKind('```python\nprint(1)\n```')).toBe('markdown')
    expect(looksLikeMarkdown('今天天气不错，适合出门。')).toBe(false)
    expect(detectArtifactKind('今天天气不错，适合出门。')).toBe('text')
  })

  it('空文本 / 纯空白 → text', () => {
    expect(detectArtifactKind('')).toBe('text')
    expect(detectArtifactKind('   \n  ')).toBe('text')
  })

  it('评审 F6：行首带空格的 "@@ …"（非行首）不作 diff 证据', () => {
    expect(isUnifiedDiff('前置说明\n @@ -1 +1 @@\n普通文本\n')).toBe(false)
  })
})

describe('diff 行解析 parseDiffLines', () => {
  it('hunk 头重置游标；ctx 双增、del 只增旧、add 只增新', () => {
    const lines = parseDiffLines('@@ -10,3 +20,4 @@\n ctx1\n-del1\n+add1\n+add2\n ctx2\n')
    expect(lines.map((l) => l.type)).toEqual(['hunk', 'ctx', 'del', 'add', 'add', 'ctx'])
    expect(lines[1]).toMatchObject({ oldNo: 10, newNo: 20 })
    expect(lines[2]).toMatchObject({ oldNo: 11, newNo: null })
    expect(lines[3]).toMatchObject({ oldNo: null, newNo: 21 })
    expect(lines[4]).toMatchObject({ oldNo: null, newNo: 22 })
    expect(lines[5]).toMatchObject({ oldNo: 12, newNo: 23 })
  })

  it('首个 hunk 之前（含 diff --git / --- / +++ 头）记为 meta 不占行号', () => {
    const lines = parseDiffLines('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n')
    const metas = lines.filter((l) => l.type === 'meta')
    expect(metas).toHaveLength(3)
    expect(metas.every((l) => l.oldNo === null && l.newNo === null)).toBe(true)
    expect(lines[4]).toMatchObject({ type: 'del', oldNo: 1 })
    expect(lines[5]).toMatchObject({ type: 'add', newNo: 1 })
  })

  it('hunk 内空行按上下文行计数（不吞行号）', () => {
    const lines = parseDiffLines('@@ -5,2 +5,2 @@\n\n+x\n')
    expect(lines[1]).toMatchObject({ type: 'ctx', oldNo: 5, newNo: 5 })
    expect(lines[2]).toMatchObject({ type: 'add', newNo: 6 })
  })

  // 评审 F6：hunk 内两处微边界
  it('hunk 内增行内容以 ++ 开头（原文 +++）→ 归 add 不吞成 meta', () => {
    const lines = parseDiffLines('@@ -1,1 +1,2 @@\n+a\n+++ b\n')
    expect(lines[1]).toMatchObject({ type: 'add', text: 'a', newNo: 1 })
    expect(lines[2]).toMatchObject({ type: 'add', text: '++ b', newNo: 2 })
    expect(lines.some((l) => l.type === 'meta')).toBe(false)
  })

  it('hunk 内形如 " @@ -N +N @@" 的上下文行归 ctx（不误判 hunk、不重置游标）', () => {
    // 输入必须与真 hunk 头同形（含数字）才能杀死 trimStart 旧判定
    const lines = parseDiffLines('@@ -5,2 +5,2 @@\n @@ -99 +99 @@\n-x\n')
    expect(lines[1]).toMatchObject({ type: 'ctx', text: '@@ -99 +99 @@', oldNo: 5, newNo: 5 })
    expect(lines[2]).toMatchObject({ type: 'del', oldNo: 6 })
    expect(lines.filter((l) => l.type === 'hunk')).toHaveLength(1)
  })

  it('第二个文件的 ---/+++ 头（hunk 之间）恢复 meta 语义', () => {
    const text = 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n'
      + 'diff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-c\n+d\n'
    const lines = parseDiffLines(text)
    expect(lines.filter((l) => l.type === 'meta')).toHaveLength(6)
    expect(lines.find((l) => l.type === 'del' && l.text === 'c')).toBeTruthy()
    expect(lines.find((l) => l.type === 'add' && l.text === 'd')).toBeTruthy()
  })
})

describe('artifactFilename / downloadTextFile', () => {
  it('文件名按类型给扩展名并 sanitize', () => {
    expect(artifactFilename('search', 'json')).toBe('search.json')
    expect(artifactFilename('run_report', 'markdown')).toBe('run_report.md')
    expect(artifactFilename('git diff', 'diff')).toBe('git_diff.patch')
    expect(artifactFilename('', 'text')).toBe('artifact.txt')
    expect(artifactFilename('a/b\\c*d?', 'text')).toBe('a_b_c_d.txt')
  })

  beforeEach(() => {
    // jsdom 未实现 createObjectURL/revokeObjectURL：补最小桩
    URL.createObjectURL = vi.fn(() => 'blob:mock')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('downloadTextFile：创建 <a download> 并点击；json 先美化', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    downloadTextFile('result.json', '{"a":1}', 'json')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const a = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(a.download).toBe('result.json')
    const blob = (URL.createObjectURL as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob
    expect(blob.type).toContain('text/plain')
  })

  it('downloadTextFile：json 不可解析时原样下载（不抛错）', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    expect(() => downloadTextFile('x.json', '{"a":', 'json')).not.toThrow()
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })
})
