from pathlib import Path
base = Path(r'D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome')
for lang in ('zh','en','ja','fr'):
    target = base / 'frontend/src/locales' / f'{lang}.ts'
    new_block = (base / '.probe/i18n' / f'{lang}.txt').read_text(encoding='utf-8')
    text = target.read_text(encoding='utf-8')
    start_marker = '\n  firsthome: {'
    start = text.index(start_marker)
    end_marker = '\n  menu: {'
    end = text.index(end_marker, start)
    text = text[:start] + '\n' + new_block.rstrip('\n') + text[end:]
    target.write_text(text, encoding='utf-8', newline='')
    print(lang, 'replaced', start, end)
