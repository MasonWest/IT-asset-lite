"""CSV / xlsx 导出与解析的公共件。

三个模块（盘点导出、资产导出、审计导出）都要做同一件麻烦事：
中文文件名、Excel 打开不乱码的 BOM、表头样式与列宽。
各写一份的结局必然是「盘点导出的表头有底色、资产导出的没有」这类不一致，
所以统一收在这里。

关于中文文件名的坑（踩过一次就忘不了）：
HTTP 头是 latin-1 编码的，直接把「盘点.xlsx」塞进 filename= 会抛 UnicodeEncodeError，
或者被浏览器/代理改成一串乱码。正确做法是 RFC 5987 的 filename*=UTF-8''<百分号编码>，
同时留一个 ASCII 的 filename= 给老浏览器兜底。
"""
from __future__ import annotations

import csv
import io
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv; charset=utf-8"


def content_disposition(filename: str, fallback: str = "export") -> str:
    """生成 Content-Disposition。中文名走 filename*，ASCII 兜底。"""
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quoted}"


def safe_filename(name: str, default: str = "export") -> str:
    """去掉 Windows 文件名里非法的字符，防止导入的文件名把导出的文件名搞坏。"""
    cleaned = "".join(c for c in (name or "") if c not in '\\/:*?"<>|').strip()
    return cleaned or default


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def build_csv(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """带 BOM 的 UTF-8 CSV。

    BOM 不能省：Excel 打开无 BOM 的 UTF-8 CSV 时按本地编码解码，中文全是乱码。
    这也是为什么这里返回 bytes 而不是 str —— 必须精确控制前三个字节。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([_stringify(h) for h in headers])
    for row in rows:
        writer.writerow([_stringify(v) for v in row])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


@dataclass
class Sheet:
    """一个工作表。

    header=True 时第一行当表头：加底色、加粗、居中、冻结首行。
    header=False 时当成 key-value 清单（比如「汇总」sheet），只把第一列加粗。
    """

    title: str
    rows: list[list[Any]] = field(default_factory=list)
    widths: Optional[list[int]] = None
    header: bool = True
    bold_first_col: bool = False


def build_xlsx(sheets: Sequence[Sheet]) -> bytes:
    """生成 xlsx。样式统一走这里，别在业务代码里再写一遍 openpyxl 样式。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="EEF3FF")
    head_font = Font(bold=True, color="1F2329")

    for index, spec in enumerate(sheets):
        ws = wb.active if index == 0 else wb.create_sheet()
        ws.title = spec.title[:31] or f"Sheet{index + 1}"

        for row in spec.rows:
            ws.append([_stringify(v) for v in row] if spec.header else list(row))

        has_head = spec.header and spec.rows
        if has_head:
            for cell in ws[1]:
                cell.fill = head_fill
                cell.font = head_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.freeze_panes = "A2"
        if spec.bold_first_col:
            for cell in ws["A"]:
                cell.font = Font(bold=True)

        for idx, width in enumerate(spec.widths or [], start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def decode_table(content: bytes) -> str:
    """把上传的 csv 字节解码成文本。

    三种尝试，顺序有讲究：
      1. utf-8-sig —— 我们自己导出的 CSV 就带 BOM，也兼容无 BOM 的 utf-8；
      2. gbk       —— 中文版 Excel「另存为 CSV」默认就是 GBK，
                      不认这个，用户拿 Excel 存一遍再上传就全乱码；
      3. latin-1   —— 兜底，保证任何字节都不抛异常（宁可乱码也别 500）。
    """
    for encoding in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def detect_delimiter(text: str) -> str:
    """猜分隔符。中文 Excel 导出的「CSV」有逗号也有制表符两种，都得认。"""
    sample = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        return ","


def read_rows(content: bytes, filename: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """把上传的 xlsx / csv 读成 (表头, [(Excel 行号, 单元格列表)])。

    行号从 1 开始且按 Excel 的行号算 —— 报错时告诉用户"第 7 行有问题"，
    他才能在 Excel 里直接定位；给个内部索引 0 等于没说。
    """
    name = (filename or "").lower()

    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        raw: list[tuple[int, list[str]]] = []
        for row_number, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = ["" if v is None else str(v).strip() for v in row]
            if any(cells):
                raw.append((row_number, cells))
        wb.close()
    else:
        text = decode_table(content)
        delimiter = detect_delimiter(text)
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        raw = []
        for row_number, row in enumerate(reader, start=1):
            cells = [(c or "").strip() for c in row]
            if any(cells):
                raw.append((row_number, cells))

    if not raw:
        return [], []

    headers = [h.lstrip("\ufeff").strip() for h in raw[0][1]]
    return headers, raw[1:]
