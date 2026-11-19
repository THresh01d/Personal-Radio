"""数据库护栏自身使用假连接验证映射，避免失败测试也触碰正式数据库。"""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from app import audio_features
from db_isolation import DatabaseIsolation


@pytest.fixture
def guard(tmp_path):
    root = tmp_path / "fake project"
    directory = tmp_path / "isolated"
    root.mkdir(); directory.mkdir()
    return DatabaseIsolation(root, directory, directory / "library.db", lambda target, *a, **kw: (target, a, kw))


@pytest.mark.parametrize("style", ["path", "string", "bytes", "relative", "normalized"])
def test_plain_root_database_aliases_are_redirected_without_opening(guard,monkeypatch,style):
    monkeypatch.chdir(guard.root)
    database=guard.root/"library.db"
    if style=="string": database=str(database)
    if style=="bytes": database=os.fsencode(database)
    if style=="relative": database="./library.db"
    if style=="normalized": database=guard.root/"child"/".."/"library.db"
    target,_,_=guard.connect(database)
    assert target==guard.library_database
    assert guard.redirected_calls==1


@pytest.mark.parametrize("style", ["absolute", "relative", "localhost"])
def test_uri_flags_and_encoded_spaces_are_preserved(guard,monkeypatch,style):
    monkeypatch.chdir(guard.root)
    uri=(guard.root/"library.db").as_uri()
    if style=="relative": uri="file:library.db"
    if style=="localhost": uri=uri.replace("file:///","file://localhost/",1)
    target,args,kwargs=guard.connect(uri+"?mode=ro&immutable=1",uri=True,timeout=3)
    assert target==guard.library_database.as_uri()+"?mode=ro&immutable=1"
    assert args==() and kwargs=={"uri":True,"timeout":3}


@pytest.mark.parametrize("filename", ["conversation.db","settings.db","background.db","knowledge.db","new.sqlite3"])
def test_other_root_databases_are_isolated(guard,filename):
    target,_,_=guard.connect(guard.root/filename)
    assert target==guard.directory/("default-"+filename)


@pytest.mark.parametrize("database,kwargs", [
    (":memory:",{}),("",{}),("file:shared?mode=memory&cache=shared",{"uri":True}),
])
def test_memory_and_ephemeral_databases_keep_their_semantics(guard,database,kwargs):
    target,_,_=guard.connect(database,**kwargs)
    assert target==database and guard.redirected_calls==0


def test_explicit_temporary_database_keeps_its_path(guard):
    path=guard.directory/"specific.db"
    assert guard.connect(path)[0]==path
    assert guard.connect(path.as_uri()+"?mode=ro",uri=True)[0]==path.as_uri()+"?mode=ro"
    assert guard.redirected_calls==0


def test_unsupported_or_remote_uri_fails_before_connector(guard):
    with pytest.raises(AssertionError,match="uri=True"):
        guard.connect((guard.root/"library.db").as_uri())
    with pytest.raises(AssertionError,match="远程"):
        guard.connect("file://remote-server/shared.db",uri=True)


def test_root_default_stays_isolated_even_if_file_resolves_to_another_directory(guard,monkeypatch):
    real_resolve=Path.resolve
    formal=guard.root/"library.db"
    def linked(path,*args,**kwargs):
        # 用解析结果替身覆盖链接边界，不创建系统符号链接或打开目标文件。
        if path==formal: return guard.directory/"external-library.db"
        return real_resolve(path,*args,**kwargs)
    monkeypatch.setattr(Path,"resolve",linked)
    assert guard.connect(formal)[0]==guard.library_database


def test_captured_production_default_uses_only_synthetic_database(_isolated_library,monkeypatch):
    guard=_isolated_library
    real=guard.connector
    def checked(target,*args,**kwargs):
        # 最后一层真实连接前再断言；护栏退化时，本测试也不会打开正式路径。
        raw=unquote(urlsplit(str(target)).path) if kwargs.get("uri") else str(target)
        if os.name=="nt" and raw.startswith("/") and len(raw)>2 and raw[2]==":": raw=raw[1:]
        assert Path(raw).resolve().is_relative_to(guard.directory)
        return real(target,*args,**kwargs)
    monkeypatch.setattr(guard,"connector",checked)
    summary=dict(energy_mean=.4,energy_peak=.8,low_ratio=.5,brightness=.2,
                 dynamic_range=.3,beat_density=80,sample_seconds=20)
    saved=audio_features.record_audio_features("1",summary)
    assert saved["observation_count"]==1
    assert guard.redirected_calls>0
    with guard.connect((guard.root/"library.db").as_uri()+"?mode=ro",uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]==6
