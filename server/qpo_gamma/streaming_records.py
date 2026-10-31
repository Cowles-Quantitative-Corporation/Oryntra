"""Bounded-memory JSON-array and gzip JSONL reads; no truncated-prefix success."""
import gzip,json
from pathlib import Path


def iter_json_array(path,chunk_size=1024*1024,max_record_bytes=16*1024*1024):
    decoder=json.JSONDecoder();buffer='';started=False;comma=False;after_separator=False
    with Path(path).open() as stream:
        while True:
            buffer=buffer.lstrip()
            if not buffer:
                piece=stream.read(chunk_size)
                if not piece:raise ValueError('Truncated JSON array')
                buffer+=piece;continue
            if not started:
                if buffer[0]!='[':raise ValueError('JSON array required')
                started=True;buffer=buffer[1:];continue
            if buffer[0]==']':
                if after_separator:raise ValueError('Trailing JSON comma')
                if buffer[1:].strip():raise ValueError('Trailing content after JSON array')
                while True:
                    tail=stream.read(chunk_size)
                    if not tail:break
                    if tail.strip():raise ValueError('Trailing content after JSON array')
                return
            if comma:
                if buffer[0]!=',':raise ValueError('Missing JSON array separator')
                buffer=buffer[1:].lstrip();comma=False;after_separator=True
                if buffer.startswith(']'):raise ValueError('Trailing JSON comma')
            try:
                value,end=decoder.raw_decode(buffer)
            except json.JSONDecodeError as exc:
                piece=stream.read(chunk_size)
                if not piece:raise ValueError('Truncated or invalid JSON record') from exc
                buffer+=piece
                if len(buffer)>max_record_bytes:raise ValueError('Oversized JSON record')
                continue
            if not isinstance(value,dict):raise ValueError('JSON record object required')
            yield value;buffer=buffer[end:];comma=True;after_separator=False


def iter_records(path):
    path=Path(path)
    if path.name.endswith('.jsonl.gz'):
        with gzip.open(path,'rt') as stream:
            for line in stream:
                value=json.loads(line)
                if not isinstance(value,dict):raise ValueError('JSONL object required')
                yield value
    else:yield from iter_json_array(path)
