import sys
import importlib

print('Python', sys.version)
for m in ['torch','faiss','transformers','fastapi','uvicorn']:
    try:
        importlib.import_module(m)
        print(m, 'OK')
    except Exception as e:
        print(m, 'FAIL', e)