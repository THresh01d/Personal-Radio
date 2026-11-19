"""本地曲库数据库的结构初始化。

普通用户第一次启动时没有 ``library.db``，但扫码同步不应该依赖开发者先运行
CSV 建库脚本。这里把「创建空表」从「导入某一份 CSV」中拆出来，启动、同步、
脚本都可以安全重复调用。
"""

import sqlite3
from pathlib import Path
from app.paths import DATA_ROOT
from app.embedding_store import ensure_embedding_schema


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = DATA_ROOT / "library.db"


def ensure_library_schema(db_path=DB_PATH):
    """幂等创建一套可用的空曲库，并返回数据库路径。

    不在这里加载模型或导入歌曲：建表必须足够快，保证后端即使没有网络、
    没有 API Key，也能先正常启动并让用户进入扫码流程。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        # 独立来源表不改旧表列数、不回填旧版本，兼容既有数据库与 INSERT VALUES。
        ensure_embedding_schema(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS songs (
                song_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                aliases TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS song_embeddings (
                song_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS song_profiles (
                song_id TEXT PRIMARY KEY,
                genre TEXT,
                mood TEXT,
                scene TEXT,
                is_instrumental INTEGER,
                language TEXT,
                source TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS library_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- 网易云歌单快照与来源关系独立于 songs：同一首歌可以来自多个歌单，
            -- 后续接入其他 Provider 时也不会把平台状态塞进统一歌曲实体。
            CREATE TABLE IF NOT EXISTS ncm_playlists (
                playlist_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                track_count INTEGER NOT NULL DEFAULT 0,
                synced_track_count INTEGER NOT NULL DEFAULT 0,
                remote_updated_at INTEGER NOT NULL DEFAULT 0,
                cover_url TEXT,
                is_liked INTEGER NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ncm_playlist_songs (
                playlist_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL,
                PRIMARY KEY (playlist_id, song_id),
                FOREIGN KEY (playlist_id) REFERENCES ncm_playlists(playlist_id)
                    ON DELETE CASCADE
            );

            -- 同步任务必须落盘，页面刷新或服务重启后仍能解释上一次停在哪里。
            CREATE TABLE IF NOT EXISTS ncm_sync_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                scope TEXT NOT NULL,
                playlist_ids TEXT NOT NULL DEFAULT '[]',
                replace_library INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                skipped_playlists INTEGER NOT NULL DEFAULT 0,
                completed_playlists INTEGER NOT NULL DEFAULT 0,
                failed_playlists INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                failed_playlist_ids TEXT NOT NULL DEFAULT '[]',
                recoverable INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);
            CREATE INDEX IF NOT EXISTS idx_songs_title ON songs(title);
            CREATE INDEX IF NOT EXISTS idx_ncm_playlist_songs_song
                ON ncm_playlist_songs(song_id);
            CREATE INDEX IF NOT EXISTS idx_ncm_sync_tasks_created
                ON ncm_sync_tasks(created_at DESC);
            """
        )
        # 老数据库已经有 ncm_playlists 时，CREATE TABLE IF NOT EXISTS 不会补列；
        # 用轻量迁移保留原快照，并从来源关系回填真实同步数量。
        playlist_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ncm_playlists)")
        }
        if "synced_track_count" not in playlist_columns:
            conn.execute(
                "ALTER TABLE ncm_playlists ADD COLUMN synced_track_count INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """UPDATE ncm_playlists
               SET synced_track_count=(
                   SELECT COUNT(1) FROM ncm_playlist_songs ps
                   WHERE ps.playlist_id=ncm_playlists.playlist_id
               )
               WHERE synced_track_count=0"""
        )
        from app.platform_metadata import ensure_schema
        ensure_schema(conn)
        task_columns = {r[1] for r in conn.execute("PRAGMA table_info(ncm_sync_tasks)")}
        for column in ("metadata_processed", "metadata_total", "metadata_failed"):
            if column not in task_columns:
                conn.execute(f"ALTER TABLE ncm_sync_tasks ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
    return db_path


def library_summary(db_path=DB_PATH):
    """返回首次启动页需要的轻量统计，不加载 embedding 到内存。"""
    ensure_library_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        songs = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        embeddings = conn.execute("SELECT COUNT(*) FROM song_embeddings").fetchone()[0]
        profiles = conn.execute("SELECT COUNT(*) FROM song_profiles").fetchone()[0]
        from app.platform_metadata import coverage
        tag_coverage = coverage(conn)
    return {
        "tag_coverage": tag_coverage,
        "basic_ready": songs > 0 and embeddings == songs,
        "song_count": songs,
        "embedding_count": embeddings,
        "profile_count": profiles,
        "ready": songs > 0 and embeddings == songs,
    }
