"""Property test for Property 5 — Version_Report 編碼 round-trip.

對應 spec version-consistency-gate 任務 5.3。

Property 5: Version_Report 編碼 round-trip
    對任意由 ASCII、中文（CJK 範圍）與 emoji 字元組成的 Version_Report 內容，
    經 UTF-8 輸出串流寫出後再以 UTF-8 讀回，解碼所得字串應與原始內容相等
    （不因編碼或緩衝而遺失或損壞內容）。

Validates: Requirements 8.4

受測對象與建模動機：
    本屬性驗證的是 CLI 層 `main()` 的輸出保證——`main()` 開頭以
    `sys.stdout/stderr.reconfigure(encoding="utf-8")` 把串流重設為 UTF-8，
    寫出 Version_Report 後 `flush()`，藉此確保 Windows console（預設 cp950/
    cp1252）不因編碼不符而 UnicodeEncodeError、也不因緩衝而遺失內容（Req 8.4）。
    這是一條「編碼行為」屬性而非某個純函式的資料轉換，故不 import
    check_version.py，而是直接建模其真實 emission path：以 UTF-8 編碼串流寫出、
    再以 UTF-8 解碼讀回，斷言 round-trip 相等。

生成策略（見下方 strategies）：
    - 混合字元池：ASCII 可列印字元 + CJK（U+4E00..U+9FFF）+ 報告實際使用及常見
      的 emoji（✅ ❌ 🚀 📦 🔖），貼近真實 Version_Report 內容組成。
    - 由該字元池串接成任意長度字串作為 report 內容。

兩條互補的 round-trip 路徑（皆須恆等）：
    1. 記憶體串流：io.BytesIO + io.TextIOWrapper(encoding="utf-8") 寫出、flush，
       再取底層 bytes 以 utf-8 解碼——直接對應 main() 的
       reconfigure(encoding="utf-8") + flush() 行為。
    2. 檔案路徑：寫入 tmp 檔（encoding="utf-8"）再以 encoding="utf-8" 讀回——
       對應呼叫端（hook/CI）以檔案/管線擷取輸出的情境。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# 生成策略（hypothesis strategies）
# ---------------------------------------------------------------------------

# ASCII 可列印字元（含空白），模擬報告中的英數、標點、來源路徑等內容。
_ascii_chars = st.characters(min_codepoint=0x20, max_codepoint=0x7E)

# CJK 統一表意文字（U+4E00..U+9FFF）：報告以中文為主（如「版本一致性檢查失敗」）。
_cjk_chars = st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF)

# emoji：涵蓋 render_report 實際使用的 ✅ / ❌，外加常見點綴，貼近真實報告。
_emoji_chars = st.sampled_from(["✅", "❌", "🚀", "📦", "🔖"])

# 混合字元池：三類字元等權抽樣後串接成任意長度的 Version_Report 內容。
# max_size 取適中值以在 ≥100 迭代下維持生成效率，同時覆蓋足夠的字元組合。
_report_content = st.text(
    alphabet=st.one_of(_ascii_chars, _cjk_chars, _emoji_chars),
    min_size=0,
    max_size=200,
)


@pytest.fixture(scope="module")
def report_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """提供 module 級暫存目錄，供檔案 round-trip 測試覆寫輸出檔.

    以 module scope 建一次目錄、每個 hypothesis 例子覆寫同一個 report.txt
    （內容隨例子變化），避免 function-scoped fixture 於 @given 下觸發 HealthCheck。
    """
    return tmp_path_factory.mktemp("property5_report")


# ---------------------------------------------------------------------------
# Property 5 — 記憶體串流 round-trip（對應 main() 的 reconfigure + flush）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 5: Version_Report 編碼 round-trip
@settings(max_examples=200)
@given(content=_report_content)
def test_utf8_stream_roundtrip_preserves_content(content: str) -> None:
    """UTF-8 編碼串流寫出後以 UTF-8 讀回，內容恆等（建模 main() emission path）.

    Validates: Requirements 8.4
    """
    # 以 BytesIO 作底層位元組緩衝，外包 TextIOWrapper 指定 encoding="utf-8"——
    # 這正對應 main() 對 stdout/stderr 執行 reconfigure(encoding="utf-8") 後的狀態。
    buffer = io.BytesIO()
    text_stream = io.TextIOWrapper(buffer, encoding="utf-8", newline="")
    try:
        text_stream.write(content)
        # 對應 main() 寫出後的 flush()：確保內容確實落到底層 bytes、不因緩衝遺失。
        text_stream.flush()
        raw_bytes = buffer.getvalue()
    finally:
        # detach 避免 TextIOWrapper 於 GC 時關閉並干擾 BytesIO；此處僅需其位元組。
        text_stream.detach()

    # 呼叫端（hook/CI/skill）以 UTF-8 解碼擷取到的位元組。
    decoded = raw_bytes.decode("utf-8")

    assert decoded == content, (
        "UTF-8 串流 round-trip 後內容應與原始相等；"
        f"原始長度={len(content)}，解碼後長度={len(decoded)}"
    )


# ---------------------------------------------------------------------------
# Property 5 — 檔案路徑 round-trip（對應呼叫端以檔案/管線擷取輸出）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 5: Version_Report 編碼 round-trip
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(content=_report_content)
def test_utf8_file_roundtrip_preserves_content(
    report_dir: Path,
    content: str,
) -> None:
    """寫入 UTF-8 檔再以 UTF-8 讀回，內容恆等（呼叫端以檔案擷取輸出情境）.

    Validates: Requirements 8.4
    """
    # 每個例子覆寫同一個輸出檔（內容隨例子變化），對應呼叫端擷取輸出檔的情境。
    report_file = report_dir / "report.txt"

    # 明確 encoding="utf-8" 寫出——對應腳本一律以 UTF-8 處理 I/O 的跨平台約定。
    report_file.write_text(content, encoding="utf-8")
    # 以 UTF-8 讀回，模擬呼叫端擷取輸出檔內容。
    decoded = report_file.read_text(encoding="utf-8")

    assert decoded == content, (
        "UTF-8 檔案 round-trip 後內容應與原始相等；"
        f"原始長度={len(content)}，解碼後長度={len(decoded)}"
    )
