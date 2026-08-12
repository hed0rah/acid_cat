"""A library's schema version must be reported from the DB, and a migration
must say it is happening.

Both bugs here came out of one false alarm. A real 32,000-row library was
migrated v2 -> v3 by an MCP call simply opening it. The migration succeeded in
about a second, printed nothing, and left the registry still claiming version 1
because the cached copy was only ever rewritten by a full indexing run. The
visible state afterwards was a client that had paused and a registry that said
the migration had never happened, which is a bug report for a deadlock that did
not exist.

So: the registry reports what the file says, and a migration announces itself on
stderr (never stdout -- these calls sit under `--json` consumers).
"""

import sqlite3

import pytest

from acidcat.core.catalogue import index as idx
from acidcat.core.catalogue import paths, registry as reg


@pytest.fixture
def reg_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "registry.db"))
    conn = reg.open_registry()
    yield conn
    conn.close()


def _downgrade(db_path, version):
    """Rewrite the on-disk schema_version so the next open must migrate."""
    con = sqlite3.connect(db_path)
    con.execute("UPDATE meta SET v = ? WHERE k = 'schema_version'",
                (str(version),))
    con.commit()
    con.close()


def _registry_version(conn, root):
    row = conn.execute(
        "SELECT schema_version FROM libraries WHERE root_path = ?",
        (paths.normalize(root),)).fetchone()
    return row["schema_version"]


class TestRegistryReportsTheDbsVersion:
    def test_reattach_reads_the_version_off_the_db(self, reg_conn, tmp_path):
        """The number in the registry comes from the file, not from the caller.

        register_library is told version 1 and the DB on disk is current. The
        DB wins: it is the only one of the two that cannot be wrong.
        """
        root = tmp_path / "lib"
        root.mkdir()
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()

        reg.register_library(reg_conn, str(root), "lib", str(db),
                             schema_version=1)

        assert _registry_version(reg_conn, str(root)) == idx.SCHEMA_VERSION

    def test_a_migration_by_open_updates_the_registry(self, reg_conn, tmp_path):
        """The exact sequence that produced the false alarm.

        A library is registered while old, then migrated by nothing more than
        being opened -- which is what a query or an MCP call does. Refreshing
        stats has to notice.
        """
        root = tmp_path / "lib"
        root.mkdir()
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()
        _downgrade(db, 2)

        reg.register_library(reg_conn, str(root), "lib", str(db))
        assert _registry_version(reg_conn, str(root)) == 2, (
            "precondition: the registry should first agree with the old file")

        idx.open_db(str(db)).close()                 # migrates v2 -> current
        reg._refresh_stats_from_db(reg_conn, str(root), str(db))

        assert _registry_version(reg_conn, str(root)) == idx.SCHEMA_VERSION

    def test_a_foreign_db_does_not_clear_a_known_version(self, reg_conn,
                                                         tmp_path):
        """Absence of a readable version must not overwrite a good one.

        update_stats skips None fields on purpose; this pins that the missing
        -> None path stays a skip rather than becoming a NULL write.
        """
        root = tmp_path / "lib"
        root.mkdir()
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()
        reg.register_library(reg_conn, str(root), "lib", str(db))

        con = sqlite3.connect(str(db))
        con.execute("DELETE FROM meta WHERE k = 'schema_version'")
        con.commit()
        con.close()

        reg._refresh_stats_from_db(reg_conn, str(root), str(db))
        assert _registry_version(reg_conn, str(root)) == idx.SCHEMA_VERSION


class TestRefreshStatsCommand:
    """`index --refresh-stats` exists to repair a stale registry, so it is the
    one command that must not have its own drifted copy of the read."""

    def test_it_repairs_a_stale_schema_version(self, tmp_path, monkeypatch,
                                               capsys):
        monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "registry.db"))
        root = tmp_path / "lib"
        root.mkdir()
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()
        _downgrade(db, 1)

        conn = reg.open_registry()
        reg.register_library(conn, str(root), "lib", str(db))
        assert _registry_version(conn, str(root)) == 1
        conn.close()

        from acidcat.commands import index as index_cmd
        assert index_cmd._cmd_refresh_stats(None, None, quiet=True) == 0

        conn = reg.open_registry()
        try:
            assert _registry_version(conn, str(root)) == idx.SCHEMA_VERSION
        finally:
            conn.close()

    def test_it_still_reports_the_counts(self, tmp_path, monkeypatch, capsys):
        """Delegating the read must not cost the per-library line its numbers."""
        monkeypatch.setenv("ACIDCAT_REGISTRY", str(tmp_path / "registry.db"))
        root = tmp_path / "lib"
        root.mkdir()
        db = tmp_path / "lib.db"
        conn = idx.open_db(str(db))
        conn.execute("INSERT INTO scan_roots (path) VALUES ('/x')")
        conn.execute("INSERT INTO samples (path, scan_root, mtime, size, "
                     "format) VALUES ('/x/a.wav', '/x', 0, 0, 'wav')")
        conn.commit()
        conn.close()

        rconn = reg.open_registry()
        reg.register_library(rconn, str(root), "lib", str(db))
        rconn.close()
        capsys.readouterr()

        from acidcat.commands import index as index_cmd
        index_cmd._cmd_refresh_stats(None, None)

        err = capsys.readouterr().err
        assert "samples=1" in err and "features=0" in err


class TestMigrationAnnouncesItself:
    def test_it_says_so_on_stderr(self, tmp_path, capsys):
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()
        _downgrade(db, 1)

        idx.open_db(str(db)).close()

        cap = capsys.readouterr()
        assert cap.out == "", "machine-readable stdout must stay clean"
        assert "migrating" in cap.err
        assert f"v{idx.SCHEMA_VERSION}" in cap.err

    def test_an_ordinary_open_stays_silent(self, tmp_path, capsys):
        """Announce that a migration IS happening, never that one COULD.

        Silence is what makes the message worth reading when it appears.
        """
        db = tmp_path / "lib.db"
        idx.open_db(str(db)).close()
        capsys.readouterr()

        idx.open_db(str(db)).close()

        cap = capsys.readouterr()
        assert cap.out == "" and cap.err == ""

    def test_it_states_the_size_of_the_job(self, tmp_path, capsys):
        """The cost scales with the library, so the row count is the whole
        point of the message: it is the difference between 'wait a second' and
        'wait several minutes'."""
        db = tmp_path / "lib.db"
        conn = idx.open_db(str(db))
        conn.execute("INSERT INTO scan_roots (path) VALUES ('/x')")
        for i in range(3):
            conn.execute(
                "INSERT INTO samples (path, scan_root, mtime, size, format) "
                "VALUES (?, '/x', 0, 0, 'wav')", (f"/x/{i}.wav",))
        conn.commit()
        conn.close()
        _downgrade(db, 1)
        capsys.readouterr()

        idx.open_db(str(db)).close()

        assert "3 rows" in capsys.readouterr().err
