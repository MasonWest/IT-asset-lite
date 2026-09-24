"""运行环境自检 —— 检查当前 Python 能不能直接跑起本系统。

为什么单独做一个脚本，而不是把检查写在 .bat 里：

1. 判断「装了没」要看的是 **import 名**，不是 pip 包名。这俩经常不一样：
   Pillow 要 import PIL，python-multipart 要 import multipart，
   SQLAlchemy 要 import sqlalchemy。写在 bat 里只能靠一堆 if，容易漏。
2. 缺东西的时候要说清楚"缺哪几个、怎么装"，中文提示放在 bat 里要处理
   一堆转义和引号；放 Python 里就是普通的 print。
3. 它只用标准库 —— 任何 Python 都能跑它，包括还没装依赖的那个。

用法：
    python check_env.py            人类可读的报告
    python check_env.py --quiet    不打印，只看退出码（给 .bat 探测用）

退出码：
    0  依赖齐全，可以启动
    1  缺少依赖
    2  Python 版本过低（低于 3.10）
"""

from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 10)


class Requirement:
    """一条依赖：pip 包名 / import 名 / 用来干什么 / 是否必需。"""

    __slots__ = ("package", "module", "purpose", "required")

    def __init__(self, package: str, module: str, purpose: str, *, required: bool = True) -> None:
        self.package = package
        self.module = module
        self.purpose = purpose
        self.required = required


# 这份清单和 backend/requirements.txt 一一对应。
# 两边要一起改 —— requirements.txt 是"装什么"，这里是"装完了能不能 import"。
REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement("fastapi", "fastapi", "Web 框架"),
    Requirement("uvicorn", "uvicorn", "ASGI 服务器（真正跑服务的）"),
    Requirement("SQLAlchemy", "sqlalchemy", "数据库 ORM"),
    Requirement("pydantic", "pydantic", "请求 / 响应数据校验"),
    Requirement("python-multipart", "multipart", "文件上传（批量导入）"),
    Requirement("qrcode", "qrcode", "生成资产二维码"),
    Requirement("Pillow", "PIL", "图片处理（二维码出图 / Excel 里的图）"),
    Requirement("openpyxl", "openpyxl", "Excel 导入导出（模板 / 台账 / 盘点 / 审计）"),
)

_HERE = Path(__file__).resolve().parent
_REQUIREMENTS_FILE = _HERE / "requirements.txt"


def _probe(item: Requirement) -> tuple[bool, str]:
    """试着 import 一下。返回 (能不能用, 版本号或错误说明)。

    用真 import 而不是 find_spec：装了但坏掉（比如 qrcode 缺了 pillow 后端）
    也要算不通过，光看文件在不在是查不出来的。
    """
    try:
        module = __import__(item.module)
    except Exception as exc:  # noqa: BLE001 - 任何异常都视为不可用
        return False, f"{type(exc).__name__}: {exc}"
    return True, str(getattr(module, "__version__", "") or "")


def _install_command() -> str:
    """给出可以直接复制粘贴的安装命令。"""
    return f'{sys.executable} -m pip install -r "{_REQUIREMENTS_FILE}"'


def check() -> int:
    results: list[tuple[Requirement, bool, str]] = []
    for item in REQUIREMENTS:
        ok, detail = _probe(item)
        results.append((item, ok, detail))

    missing = [item for item, ok, _ in results if not ok and item.required]
    version = ".".join(str(part) for part in sys.version_info[:3])

    print(f"Python  {sys.executable}")
    print(f"版本    {version}")
    print()
    for item, ok, detail in results:
        mark = "OK " if ok else "-- "
        # 有些包（比如 qrcode）不暴露 __version__，装上了就显示「已安装」
        shown = (detail or "已安装") if ok else "缺失"
        print(f"  {mark} {item.module:<12} {shown:<12} {item.purpose}")

    if missing:
        print()
        print(f"缺少 {len(missing)} 个依赖：{', '.join(item.package for item in missing)}")
        print()
        print("装一次就好（只需执行一次）：")
        print(f"  {_install_command()}")
        print()
        print("国内网络慢的话加个镜像：")
        print(
            "  "
            + f"{sys.executable} -m pip install "
            + "-i https://pypi.tuna.tsinghua.edu.cn/simple "
            + f'-r "{_REQUIREMENTS_FILE}"'
        )
        return 1

    print()
    print("依赖齐全，可以启动。")
    return 0


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv or "-q" in argv

    if sys.version_info < MIN_PYTHON:
        if not quiet:
            need = ".".join(str(part) for part in MIN_PYTHON)
            have = ".".join(str(part) for part in sys.version_info[:3])
            print(f"Python 版本过低：需要 {need} 或更高，当前 {have}。")
            print(f"解释器：{sys.executable}")
        return 2

    if quiet:
        # 只看退出码：不打印，也不让 import 的 warning 污染 .bat 的输出
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return check()

    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
