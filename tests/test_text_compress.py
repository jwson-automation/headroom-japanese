from headroom_ja import compress
from headroom_ja.text_compress import compress_log, compress_search, compress_diff


def test_log_keeps_errors_dedups_templates():
    lines = [f"2026-06-21T10:00:{i:02d} INFO リクエスト処理 {i}" for i in range(60)]
    lines[40] = "2026-06-21T10:00:40 ERROR 決済サービスがタイムアウトしました"
    text = "\n".join(lines)
    out, total, kept = compress_log(text)
    assert total == 60
    assert kept < total                      # repeated INFO templates collapsed
    assert "タイムアウト" in out               # error line preserved
    assert "省略" in out


def test_log_japanese_error_keyword():
    lines = [f"INFO 正常 {i}" for i in range(40)]
    lines[10] = "通信に失敗しました"
    out, _, _ = compress_log("\n".join(lines))
    assert "失敗" in out


def test_search_dedups_and_caps_per_file():
    lines = []
    for i in range(20):
        lines.append(f"src/main.py:{i}:    print('x')")
    lines.append("src/auth.py:5:    login()")
    out, total, kept = compress_search("\n".join(lines))
    assert "src/auth.py:5" in out            # distinct file's hit survives
    assert kept < total                       # main.py capped per-file
    assert "省略" in out


def test_diff_keeps_changes_trims_context():
    lines = ["diff --git a/x.py b/x.py", "@@ -1,8 +1,8 @@"]
    lines += [f" unchanged context line {i}" for i in range(30)]
    lines[15] = "-    old = 1"
    lines[16] = "+    new = 2"
    out, total, kept = compress_diff("\n".join(lines))
    assert "@@ -1,8 +1,8 @@" in out
    assert "-    old = 1" in out and "+    new = 2" in out
    assert kept < total


def test_compress_routes_log_through_pipeline():
    lines = [f"2026-06-21T10:00:{i % 60:02d} INFO 処理 {i}" for i in range(200)]
    lines[100] = "2026-06-21T10:00:00 ERROR データベース接続に失敗"
    r = compress("\n".join(lines))
    assert r.content_type == "log"
    assert r.compressed_tokens < r.original_tokens
    assert "失敗" in r.text
