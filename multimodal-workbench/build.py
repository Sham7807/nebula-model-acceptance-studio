#!/usr/bin/env python3
"""Build a dependency-free, double-clickable HTML artifact from the source files."""
from pathlib import Path
import json

root = Path(__file__).resolve().parent
html = (root / 'index.html').read_text()
html = html.replace('<link rel="stylesheet" href="style.css">', '<style>\n' + (root / 'style.css').read_text() + '\n</style>')
html = html.replace('<link rel="stylesheet" href="model-multiselect.css">', '<style>\n' + (root / 'model-multiselect.css').read_text() + '\n</style>')
discovery_script = '<script>\n' + (root / 'model-discovery.js').read_text().replace('</script', '<\\/script') + '\n</script>'
legacy_source = (root / 'legacy.html').read_text().replace('<script src="model-discovery.js"></script>', discovery_script)
legacy_source = legacy_source.replace('<link rel="stylesheet" href="model-multiselect.css">', '<style>\n' + (root / 'model-multiselect.css').read_text() + '\n</style>')
legacy_source = legacy_source.replace('<script src="model-multiselect.js"></script>', '<script>\n' + (root / 'model-multiselect.js').read_text().replace('</script', '<\\/script') + '\n</script>')
legacy = json.dumps(legacy_source, ensure_ascii=False).replace('</', '<\\/')
html = html.replace('<script src="engine.js"></script>', '<script>window.LEGACY_HTML=' + legacy + ';</script>\n<script>\n' + (root / 'engine.js').read_text().replace('</script', '<\\/script') + '\n</script>')
html = html.replace('<script src="app.js"></script>', '<script>\n' + (root / 'app.js').read_text().replace('</script', '<\\/script') + '\n</script>')
html = html.replace('<script src="media-download.js"></script>', '<script>\n' + (root / 'media-download.js').read_text().replace('</script', '<\\/script') + '\n</script>')
html = html.replace('<link rel="stylesheet" href="acceptance.css">', '<style>\n' + (root / 'acceptance.css').read_text() + '\n</style>')
html = html.replace('<link rel="stylesheet" href="history.css">', '<style>\n' + (root / 'history.css').read_text() + '\n</style>')
for script in ['model-discovery.js', 'choice-picker.js', 'model-multiselect.js', 'prompts.js', 'history-capture.js', 'acceptance.js', 'history.js']:
    html = html.replace('<script src="' + script + '"></script>', '<script>\n' + (root / script).read_text().replace('</script', '<\\/script') + '\n</script>')
output = root.parent / '中转站测试工具-多模态版.html'
output.write_text(html)
print(f'{output} ({output.stat().st_size:,} bytes)')
